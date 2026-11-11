from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time


class Store:
    def __init__(self,root: Path):
        self.root = root
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        (root/'workspaces').mkdir(exist_ok=True,mode=0o700)
        (root/'logs').mkdir(exist_ok=True,mode=0o700)
        with self.db() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY, owner TEXT NOT NULL, conversation TEXT NOT NULL,
                status TEXT NOT NULL, created INTEGER NOT NULL, expires INTEGER NOT NULL, source TEXT NOT NULL DEFAULT '{}');
              CREATE INDEX IF NOT EXISTS workspace_owner ON workspaces(owner,conversation);
              CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, workspace TEXT NOT NULL, owner TEXT NOT NULL,
                status TEXT NOT NULL, started INTEGER NOT NULL, finished INTEGER, exit_code INTEGER,
                argv TEXT NOT NULL, truncated INTEGER NOT NULL DEFAULT 0, container TEXT);
              CREATE TABLE IF NOT EXISTS file_index(workspace TEXT NOT NULL,path TEXT NOT NULL,hash TEXT NOT NULL,
                symbols TEXT NOT NULL, PRIMARY KEY(workspace,path));
              CREATE TABLE IF NOT EXISTS baseline_files(workspace TEXT NOT NULL,path TEXT NOT NULL,hash TEXT NOT NULL,
                content TEXT NOT NULL,PRIMARY KEY(workspace,path));
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.root/'state.sqlite',timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def workspace(self,ctx,workspace_id,*,ready=True,allow_expired=False):
        if ctx['workspace']!=workspace_id:
            raise PermissionError('이 작업공간을 사용할 권한이 없습니다.')
        with self.db() as db:
            row = db.execute('SELECT * FROM workspaces WHERE id=? AND owner=? AND conversation=?',
                (workspace_id,ctx['sub'],ctx['conversation'])).fetchone()
        if not row or row['expires']<=time.time() and not allow_expired or row['status']=='closed':
            raise ValueError('작업공간이 없거나 만료되었습니다.')
        if ready and row['status']!='ready':
            raise ValueError('저장소를 먼저 가져와 주세요.')
        return dict(row)

    def repo(self,workspace_id):
        return self.root/'workspaces'/workspace_id/'repo'

    def job(self,ctx,job_id,*,allow_expired=False):
        with self.db() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=? AND owner=?',(job_id,ctx['sub'])).fetchone()
        if not row:
            raise PermissionError('이 실행 작업을 사용할 권한이 없습니다.')
        self.workspace(ctx,row['workspace'],allow_expired=allow_expired)
        return dict(row)


def public_workspace(row):
    return {k:row[k] for k in ['id','conversation','status','created','expires']} | {'source':json.loads(row['source'])}
