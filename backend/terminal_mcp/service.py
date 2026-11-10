import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import time
from urllib.parse import urlsplit
import uuid

import httpx

from .analysis import Analyzer,inventory,safe_path,read_text
from .auth import issue
from .runner import Runner
from .store import Store,public_workspace


class TerminalService:
    def __init__(self,settings):
        self.settings=settings
        self.store=Store(settings.terminal_data_directory)
        self.runner=Runner(self.store,settings)
        self.analyzer=Analyzer(self.store,settings)
        self.checkout_locks={}

    async def create(self,ctx):
        workspace_id=ctx['workspace']
        if not ctx.get('provision'):
            raise PermissionError('작업공간 생성 권한이 없습니다.')
        uuid.UUID(workspace_id)
        await self.cleanup()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute('SELECT * FROM workspaces WHERE id=?',(workspace_id,)).fetchone()
            if existing:
                if existing['owner']!=ctx['sub'] or existing['conversation']!=ctx['conversation']:
                    raise PermissionError('작업공간의 소유자가 일치하지 않습니다.')
                return public_workspace(existing)
            count=db.execute("SELECT COUNT(*) FROM workspaces WHERE owner=? AND status!='closed'",(ctx['sub'],)).fetchone()[0]
            if count>=self.settings.terminal_max_workspaces_per_user:
                raise ValueError('작업공간 한도에 도달했습니다. 사용하지 않는 작업공간을 닫으세요.')
            now=int(time.time())
            db.execute('INSERT INTO workspaces(id,owner,conversation,status,created,expires) VALUES(?,?,?,?,?,?)',
                (workspace_id,ctx['sub'],ctx['conversation'],'empty',now,now+self.settings.terminal_workspace_ttl_seconds))
        self.store.repo(workspace_id).parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        return self.inspect(ctx,workspace_id)

    def inspect(self,ctx,workspace_id):
        row=self.store.workspace(ctx,workspace_id,ready=False)
        return public_workspace(row)|{'execution_mode':self.settings.terminal_execution_mode,
            'can_execute':ctx.get('execute',False) and self.settings.terminal_execution_mode!='disabled'}

    async def checkout(self,ctx,workspace_id,ticket):
        self.store.workspace(ctx,workspace_id,ready=False)
        if not ctx.get('provision') or not isinstance(ticket,str) or not 32<=len(ticket)<=100:
            raise PermissionError('저장소 가져오기 권한이 없습니다.')
        lock=self.checkout_locks.setdefault(workspace_id,asyncio.Lock())
        async with lock:
            if self.store.workspace(ctx,workspace_id,ready=False)['status']=='ready':
                return self.inspect(ctx,workspace_id)
            secret=self.settings.terminal_shared_secret.get_secret_value()
            token=issue(secret,user=ctx['sub'],conversation=ctx['conversation'],workspace=workspace_id,
                        audience='llm-terminal-broker',seconds=60)
            async with httpx.AsyncClient(timeout=15,follow_redirects=False,trust_env=False) as client:
                response=await client.post(self.settings.terminal_broker_url.rstrip('/')+'/workspaces/internal/checkout',
                    headers={'Authorization':'Bearer '+token},json={'ticket':ticket,'workspace_id':workspace_id})
                if not response.is_success:
                    raise ValueError('GitLab 가져오기 승인이 만료되었거나 연결이 해제되었습니다. 다시 시도하세요.')
                data=response.json()
            try:
                await self.clone(workspace_id,data)
            except BaseException:
                shutil.rmtree(self.store.repo(workspace_id),ignore_errors=True)
                with self.store.db() as db:db.execute("UPDATE workspaces SET status='failed' WHERE id=?",(workspace_id,))
                raise
            finally:
                data.pop('access_token',None)
            source={k:data[k] for k in ['project','commit','base_url','project_id']}
            with self.store.db() as db:
                db.execute("UPDATE workspaces SET status='ready',source=? WHERE id=?",(json.dumps(source),workspace_id))
            return self.inspect(ctx,workspace_id)

    async def clone(self,workspace_id,data):
        url=urlsplit(data['clone_url'])
        if url.scheme not in {'http','https'} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('올바른 GitLab 저장소 주소가 아닙니다.')
        import re
        if not re.fullmatch('[a-fA-F0-9]{40,64}',data['commit']):raise ValueError('커밋 형식을 확인하세요.')
        repo=self.store.repo(workspace_id)
        repo.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='checkout-',dir=self.store.root) as private:
            password=Path(private)/'credential'
            password.write_text(data['access_token']);password.chmod(0o600)
            helper=Path(private)/'askpass'
            helper.write_text('#!'+sys.executable+'\nimport sys\nprint("oauth2" if "Username" in sys.argv[1] else open('+repr(str(password))+').read())\n')
            helper.chmod(0o700)
            env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'HOME':private,'LANG':'C','GIT_CONFIG_NOSYSTEM':'1',
                 'GIT_CONFIG_GLOBAL':os.devnull,'GIT_TERMINAL_PROMPT':'0','GIT_ASKPASS':str(helper),'GIT_LFS_SKIP_SMUDGE':'1'}
            # CA certificates are administrator config, never taken from a repository.
            if data.get('ca_bundle'):env['GIT_SSL_CAINFO']=data['ca_bundle']
            prefix=['git','-c','http.followRedirects=false','-c','protocol.file.allow=never','-c','protocol.ext.allow=never',
                    '-c','protocol.ssh.allow=never','-c','core.hooksPath='+str(Path(private)/'disabled-hooks')]
            async with asyncio.timeout(120):
                for args in [['init'],['remote','add','origin',data['clone_url']],
                             ['fetch','--depth=1','--no-tags','origin',data['commit']],['checkout','--detach','FETCH_HEAD']]:
                    await self.git_process(prefix+args,repo,env)
        files,truncated=inventory(repo,self.settings)
        if not files:raise ValueError('분석 가능한 UTF-8 소스 파일이 없습니다.')
        import hashlib
        with self.store.db() as db:
            for path,_ in files:
                try:text=read_text(repo,path,self.settings)
                except (ValueError,OSError):continue
                db.execute('INSERT OR REPLACE INTO baseline_files VALUES(?,?,?,?)',
                    (workspace_id,path,hashlib.sha256(text.encode()).hexdigest(),text))

    async def git_process(self,args,cwd,env):
        process=await asyncio.create_subprocess_exec(*args,cwd=cwd,env=env,start_new_session=True,
            stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
        waiting=asyncio.create_task(process.wait())
        try:
            while process.returncode is None:
                await asyncio.wait({waiting},timeout=.25)
                total=sum(p.stat().st_size for p in cwd.rglob('*') if p.is_file() and not p.is_symlink())
                if total>self.settings.terminal_max_repository_bytes:
                    raise ValueError('저장소 다운로드 크기 제한을 초과했습니다.')
            if process.returncode:
                raise ValueError('GitLab 소스를 내려받지 못했습니다. 저장소 읽기 권한·네트워크·인증서를 확인하세요.')
        finally:
            if process.returncode is None:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                await process.wait()
            await asyncio.gather(waiting,return_exceptions=True)

    async def diff(self,ctx,workspace_id):
        self.store.workspace(ctx,workspace_id)
        import difflib
        root=self.store.repo(workspace_id)
        with self.store.db() as db:
            original={row['path']:row['content'] for row in db.execute('SELECT path,content FROM baseline_files WHERE workspace=?',(workspace_id,))}
        files,_=inventory(root,self.settings)
        changed=[];parts=[];size=0
        for path in sorted(set(original)|{p for p,_ in files}):
            try:now=read_text(root,path,self.settings)
            except (FileNotFoundError,ValueError):now=''
            before=original.get(path,'')
            if now==before:continue
            changed.append(path)
            for line in difflib.unified_diff(before.splitlines(True),now.splitlines(True),fromfile='a/'+path,tofile='b/'+path):
                size+=len(line.encode())
                if size>32000:return {'diff':''.join(parts),'changed_files':changed,'truncated':True}
                parts.append(line)
        return {'diff':''.join(parts),'changed_files':changed,'truncated':False,'scope':'eligible source files against downloaded snapshot'}

    async def close(self,ctx,workspace_id):
        self.store.workspace(ctx,workspace_id,ready=False,allow_expired=True)
        with self.store.db() as db:
            ids=[r[0] for r in db.execute("SELECT id FROM jobs WHERE workspace=? AND status='running'",(workspace_id,))]
        for job_id in ids:await self.runner.cancel(ctx,job_id)
        shutil.rmtree(self.store.repo(workspace_id).parent,ignore_errors=True)
        self.remove_logs(workspace_id)
        with self.store.db() as db:
            db.execute("UPDATE workspaces SET status='closed' WHERE id=?",(workspace_id,))
            db.execute('DELETE FROM file_index WHERE workspace=?',(workspace_id,))
            db.execute('DELETE FROM baseline_files WHERE workspace=?',(workspace_id,))
        return {'closed':True}

    def remove_logs(self,workspace_id):
        with self.store.db() as db:
            ids=[row[0] for row in db.execute('SELECT id FROM jobs WHERE workspace=?',(workspace_id,))]
            for job_id in ids:(self.store.root/'logs'/(job_id+'.jsonl')).unlink(missing_ok=True)
            db.execute('DELETE FROM jobs WHERE workspace=?',(workspace_id,))

    async def cleanup(self):
        with self.store.db() as db:
            rows=db.execute("SELECT * FROM workspaces WHERE expires<=? AND status!='closed'",(int(time.time()),)).fetchall()
        for row in rows:
            with self.store.db() as db:
                active=db.execute("SELECT 1 FROM jobs WHERE workspace=? AND status='running'",(row['id'],)).fetchone()
            if active:continue
            shutil.rmtree(self.store.repo(row['id']).parent,ignore_errors=True)
            self.remove_logs(row['id'])
            with self.store.db() as db:
                db.execute("UPDATE workspaces SET status='closed' WHERE id=?",(row['id'],))
                db.execute('DELETE FROM file_index WHERE workspace=?',(row['id'],))
                db.execute('DELETE FROM baseline_files WHERE workspace=?',(row['id'],))
