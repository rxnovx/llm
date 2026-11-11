"""Bounded jobs. Production execution uses disposable, credential-free containers."""
import asyncio
import codecs
import json
import os
from pathlib import Path
import re
import signal
import sys
import time
import uuid

from .analysis import safe_path


def clean(text):
    text=re.sub(r'\x1b\][^\x07]*(?:\x07|\x1b\\)','',text)
    return re.sub(r'[\x00-\x08\x0b-\x1f\x7f]','',text)


class Runner:
    def __init__(self,store,settings):
        self.store,self.settings=store,settings
        self.tasks={}

    async def recover(self):
        with self.store.db() as db:
            rows=db.execute("SELECT id,container FROM jobs WHERE status IN ('running','queued')").fetchall()
            db.execute("UPDATE jobs SET status='interrupted',finished=? WHERE status IN ('running','queued')",(int(time.time()),))
        for row in rows:
            if row['container']:
                await self.remove_container(row['container'])

    async def remove_container(self,name):
        try:
            process=await asyncio.create_subprocess_exec('docker','rm','-f',name,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(process.wait(),10)
        except (OSError,TimeoutError):
            pass

    def command(self,workspace_id,argv,cwd,job_id,tty):
        settings=self.settings
        if settings.terminal_execution_mode=='trusted_local':
            return argv,safe_path(self.store.repo(workspace_id),cwd,directory=True),None
        if settings.terminal_execution_mode!='docker':
            raise PermissionError('이 실행 서버는 코드 조회 전용입니다. 운영자가 실행 워커를 설정해야 합니다.')
        if not settings.terminal_workspace_host_path:
            raise ValueError('TERMINAL_WORKSPACE_HOST_PATH 설정이 필요합니다.')
        host=Path(settings.terminal_workspace_host_path)/'workspaces'/workspace_id/'repo'
        name='llm-job-'+job_id
        command=['docker','run','--rm','--name',name,'--network=none','--read-only','--cap-drop=ALL',
            '--security-opt=no-new-privileges','--pids-limit=128','--memory',f'{settings.terminal_memory_mb}m',
            '--cpus',str(settings.terminal_cpus),'--user','10001:10001','--tmpfs','/tmp:rw,noexec,nosuid,size=128m',
            '--mount',f'type=bind,src={host},dst=/workspace','--workdir','/workspace/'+cwd,
            '-e','HOME=/tmp','-e','PYTHONDONTWRITEBYTECODE=1','-i']
        # stdin is piped in both modes. A PTY is allocated inside the container when requested.
        if tty:command.append('-t')
        return command+[settings.terminal_worker_image,*argv],None,name

    async def start(self,ctx,workspace_id,argv,cwd='.',timeout_seconds=120,tty=False):
        self.store.workspace(ctx,workspace_id)
        if not ctx.get('execute'):
            raise PermissionError('이 요청은 분석 전용입니다. 실행 모드를 선택해 주세요.')
        if tty:raise ValueError('현재 버전은 파이프 기반 입력·출력을 지원합니다. tty=false로 실행하세요.')
        if not isinstance(argv,list) or not 1<=len(argv)<=100 or any(not isinstance(a,str) or '\x00' in a or len(a)>8000 for a in argv):
            raise ValueError('명령 인자 배열을 확인하세요.')
        if not argv[0] or sum(len(a) for a in argv)>16000:
            raise ValueError('명령 길이 제한을 초과했습니다.')
        timeout_seconds=min(max(1,timeout_seconds),self.settings.terminal_job_timeout_seconds)
        cwd_path=safe_path(self.store.repo(workspace_id),cwd,directory=True)
        if not cwd_path.is_dir():raise ValueError('작업 디렉터리를 찾을 수 없습니다.')
        job_id=str(uuid.uuid4())
        command,directory,container=self.command(workspace_id,argv,cwd,job_id,tty)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]>=self.settings.terminal_max_jobs:
                raise ValueError('실행 워커가 사용 중입니다. 잠시 후 다시 실행하세요.')
            if db.execute("SELECT 1 FROM jobs WHERE workspace=? AND status IN ('queued','running')",(workspace_id,)).fetchone():
                raise ValueError('이 작업공간에서 실행 중인 명령을 먼저 완료하거나 중지하세요.')
            db.execute('INSERT INTO jobs(id,workspace,owner,status,started,argv,container) VALUES(?,?,?,?,?,?,?)',
                (job_id,workspace_id,ctx['sub'],'running',int(time.time()),json.dumps(argv),container))
        task=asyncio.create_task(self.run(job_id,command,directory,container,timeout_seconds,tty))
        self.tasks[job_id]={'task':task,'process':None}
        task.add_done_callback(lambda _:self.tasks.pop(job_id,None))
        return {'job_id':job_id,'status':'running','timeout_seconds':timeout_seconds}

    async def run(self,job_id,command,directory,container,timeout,tty):
        process=None
        status,code='failed',None
        written,sequence=0,0
        truncated=False
        log=self.store.root/'logs'/(job_id+'.jsonl')
        def emit(stream,text):
            nonlocal written,sequence,truncated
            text=clean(text)
            remaining=self.settings.terminal_max_output_bytes-written
            if remaining<=0:
                truncated=True;return
            encoded=text.encode()
            if len(encoded)>remaining:truncated=True
            text=encoded[:remaining].decode('utf-8',errors='ignore')
            written+=len(text.encode());sequence+=1
            with log.open('a') as output:
                output.write(json.dumps({'seq':sequence,'stream':stream,'text':text},ensure_ascii=False)+'\n')
        async def drain(stream,label):
            decoder=codecs.getincrementaldecoder('utf-8')(errors='replace')
            while part:=await stream.read(4096):
                emit(label,decoder.decode(part))
            emit(label,decoder.decode(b'',final=True))
        async def quota():
            while process.returncode is None:
                await asyncio.sleep(.5)
                with self.store.db() as db:
                    workspace=db.execute('SELECT workspace FROM jobs WHERE id=?',(job_id,)).fetchone()[0]
                root=self.store.repo(workspace)
                size=0
                for directory,dirs,names in os.walk(root,followlinks=False):
                    dirs[:]=[name for name in dirs if not (Path(directory)/name).is_symlink()]
                    for name in names:
                        path=Path(directory)/name
                        try:
                            if not path.is_symlink():size+=path.stat().st_size
                        except FileNotFoundError:pass
                    if size>self.settings.terminal_max_repository_bytes:
                        raise ValueError('작업공간의 디스크 사용 한도를 초과했습니다.')
        try:
            # Never inherit application tokens, DB URLs, cloud credentials, or proxy secrets.
            env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'HOME':'/tmp','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}
            if self.settings.terminal_execution_mode=='trusted_local':
                # Termux's Python native modules may need its operator-configured runtime linker paths.
                for name in ['PREFIX','TMPDIR','LD_LIBRARY_PATH','LD_PRELOAD']:
                    if name in os.environ:env[name]=os.environ[name]
            process=await asyncio.create_subprocess_exec(*command,cwd=directory,env=env,start_new_session=True,
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            self.tasks[job_id]['process']=process
            async with asyncio.timeout(timeout):
                await asyncio.gather(drain(process.stdout,'stdout'),drain(process.stderr,'stderr'),process.wait(),quota())
            code=process.returncode
            status='succeeded' if code==0 else 'failed'
        except asyncio.CancelledError:
            status='cancelled'
        except TimeoutError:
            status='timed_out'
        except OSError:
            emit('stderr','실행 파일이나 워커를 시작하지 못했습니다. 설치된 실행 환경을 확인하세요.\n')
        except ValueError as error:
            emit('stderr',str(error)+'\n')
        finally:
            if process and process.returncode is None:
                try:os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:await asyncio.wait_for(process.wait(),2)
                except TimeoutError:
                    try:os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    await process.wait()
            if container:await self.remove_container(container)
            with self.store.db() as db:
                db.execute('UPDATE jobs SET status=?,exit_code=?,finished=?,truncated=? WHERE id=?',
                    (status,code,int(time.time()),int(truncated),job_id))

    def read(self,ctx,job_id,cursor=0,max_bytes=24000):
        row=self.store.job(ctx,job_id)
        if cursor<0 or max_bytes<1 or max_bytes>32000:raise ValueError('로그 조회 범위를 확인하세요.')
        events=[];used=0;next_cursor=cursor
        path=self.store.root/'logs'/(job_id+'.jsonl')
        if path.exists():
            with path.open() as stream:
                for line in stream:
                    try:item=json.loads(line)
                    except ValueError:continue
                    if item['seq']<=cursor:continue
                    cost=len(item['text'].encode())
                    if events and used+cost>max_bytes:break
                    events.append(item);used+=cost;next_cursor=item['seq']
                    if len(events)>=100:break
        return {'job_id':job_id,'status':row['status'],'exit_code':row['exit_code'],'events':events,
                'next_cursor':next_cursor,'truncated':bool(row['truncated'])}

    async def input(self,ctx,job_id,text='',eof=False):
        self.store.job(ctx,job_id)
        state=self.tasks.get(job_id)
        if not ctx.get('execute') or not state or not state['process'] or not state['process'].stdin:
            raise ValueError('입력 가능한 실행 작업이 없습니다.')
        if len(text.encode())>8000:raise ValueError('입력 길이 제한을 초과했습니다.')
        stream=state['process'].stdin
        try:
            if text:stream.write(text.encode());await stream.drain()
            if eof:stream.close()
        except (BrokenPipeError,ConnectionResetError):
            raise ValueError('실행 작업의 입력이 종료되었습니다.') from None
        return {'accepted':True}

    async def cancel(self,ctx,job_id):
        row=self.store.job(ctx,job_id,allow_expired=True)
        state=self.tasks.get(job_id)
        if state:
            state['task'].cancel()
            await asyncio.gather(state['task'],return_exceptions=True)
            with self.store.db() as db:
                db.execute("UPDATE jobs SET status='cancelled',finished=? WHERE id=? AND status='running'",(int(time.time()),job_id))
        return {'job_id':job_id,'status':'cancelled' if state else row['status']}

    async def shutdown(self):
        tasks=[state['task'] for state in self.tasks.values()]
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
