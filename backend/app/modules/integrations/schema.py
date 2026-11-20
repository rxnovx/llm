"""기존 OAuth 연결 ID를 유지하면서 서버별 다중 계정을 허용합니다."""

from sqlalchemy import inspect
from sqlalchemy.schema import CreateTable

from .models import AppConnection


def upgrade_connections(bind):
    """create_all 직후 호출하는 멱등적 마이그레이션. 기존 데이터는 지우지 않습니다."""
    inspector = inspect(bind)
    if not inspector.has_table('app_connections'):
        return
    old = [item for item in inspector.get_unique_constraints('app_connections')
           if item['column_names'] == ['user_id', 'provider_id']]
    if not old:
        return
    if bind.dialect.name == 'sqlite':
        # SQLite의 UNIQUE 변경은 테이블 재구성이 필요합니다. 같은 연결에서
        # FK를 일시 중지하고 데이터 복사·FK 검사·커밋을 한 트랜잭션에 묶습니다.
        raw = bind.raw_connection()
        original = raw.execute('PRAGMA foreign_keys').fetchone()[0]
        try:
            raw.execute('PRAGMA foreign_keys=OFF')
            raw.execute('BEGIN IMMEDIATE')
            # 다른 API 프로세스가 먼저 완료했으면 이중 재구성을 생략합니다.
            indexes = raw.execute('PRAGMA index_list(app_connections)').fetchall()
            legacy = any([r[2] for r in raw.execute('PRAGMA index_info("' + i[1].replace('"', '""') + '")')]
                         == ['user_id', 'provider_id'] for i in indexes if i[2])
            if not legacy:
                raw.rollback()
                return
            stored_columns = {item[1] for item in raw.execute('PRAGMA table_info(app_connections)')}
            if stored_columns != {column.name for column in AppConnection.__table__.columns}:
                raise RuntimeError('GitLab 연결 테이블에 알 수 없는 열이 있어 자동 변경하지 않았습니다.')
            secondary_objects = raw.execute("SELECT sql FROM sqlite_master WHERE tbl_name='app_connections' "
                "AND type IN ('index', 'trigger') AND sql IS NOT NULL").fetchall()
            ddl = str(CreateTable(AppConnection.__table__).compile(dialect=bind.dialect))
            ddl = ddl.replace('CREATE TABLE app_connections', 'CREATE TABLE app_connections_upgrade', 1)
            raw.execute(ddl)
            columns = ', '.join('"' + column.name + '"' for column in AppConnection.__table__.columns)
            raw.execute(f'INSERT INTO app_connections_upgrade ({columns}) SELECT {columns} FROM app_connections')
            raw.execute('DROP TABLE app_connections')
            raw.execute('ALTER TABLE app_connections_upgrade RENAME TO app_connections')
            for statement in secondary_objects:
                raw.execute(statement[0])
            if not any('ix_app_connections_user_id' in item[0] for item in secondary_objects):
                raw.execute('CREATE INDEX ix_app_connections_user_id ON app_connections(user_id)')
            if raw.execute('PRAGMA foreign_key_check').fetchone():
                raise RuntimeError('GitLab 연결 마이그레이션에서 외래 키 오류를 발견했습니다.')
            raw.commit()
        except BaseException:
            raw.rollback()
            raise
        finally:
            raw.execute(f'PRAGMA foreign_keys={int(original)}')
            raw.close()
    elif bind.dialect.name == 'postgresql':
        with bind.begin() as connection:
            connection.exec_driver_sql('LOCK TABLE app_connections IN ACCESS EXCLUSIVE MODE')
            current = inspect(connection).get_unique_constraints('app_connections')
            for constraint in current:
                if constraint['column_names'] == ['user_id', 'provider_id']:
                    name = connection.dialect.identifier_preparer.quote(constraint['name'])
                    connection.exec_driver_sql(f'ALTER TABLE app_connections DROP CONSTRAINT {name}')
            if not any(item['column_names'] == ['user_id', 'provider_id', 'external_id'] for item in current):
                connection.exec_driver_sql('ALTER TABLE app_connections ADD CONSTRAINT uq_gitlab_user_provider_identity '
                    'UNIQUE (user_id, provider_id, external_id)')
    else:
        raise RuntimeError('GitLab 다중 계정 마이그레이션은 SQLite와 PostgreSQL을 지원합니다.')
