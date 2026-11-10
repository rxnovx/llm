import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.modules.auth.models import AuditLog, User
from app.modules.chat import providers
from app.modules.chat.models import Conversation, GenerationRun as Run
from app.modules.chat.router import prepare, TurnInput


def headers(identity):
    return {'Authorization': f'Bearer {identity["access_token"]}'}


def operator(client, account):
    identity = account('operator')
    with client.db_factory() as db:
        db.get(User, identity['user']['subject']).is_global_admin = True
        db.commit()
    return identity


@pytest.mark.parametrize('path,method', [('overview', 'GET'), ('requests', 'GET'), ('users', 'GET'),
                                      ('audit', 'GET'), ('connections/demo/check', 'POST')])
def test_operations_require_current_server_side_admin(client, account, path, method):
    identity = account()
    assert not identity['user']['is_global_admin']
    assert client.request(method, f'/admin/{path}').status_code == 401
    assert client.request(method, f'/admin/{path}', headers=headers(identity)).status_code == 403
    with client.db_factory() as db:
        db.get(User, identity['user']['subject']).is_global_admin = True
        db.commit()
    assert client.request(method, f'/admin/{path}', headers=headers(identity)).status_code == 200
    with client.db_factory() as db:
        db.get(User, identity['user']['subject']).is_global_admin = False
        db.commit()
    assert client.request(method, f'/admin/{path}', headers=headers(identity)).status_code == 403


def test_profile_cannot_grant_operator_access(client, account):
    identity = account()
    result = client.patch('/users/me', headers=headers(identity), json={'display_name': 'Member', 'is_global_admin': True})
    assert result.status_code in {200, 422}
    assert not client.get('/auth/me', headers=headers(identity)).json()['is_global_admin']
    assert client.get('/admin/overview', headers=headers(identity)).status_code == 403


def test_overview_uses_attempts_filters_real_and_test_and_redacts_configuration(client, account, monkeypatch):
    identity = operator(client, account)
    account('member')
    now = int(time.time())
    monkeypatch.setenv('LLM_CONNECTIONS_JSON', json.dumps([{'id':'office','name':'Office','provider':'vllm',
        'base_url':'http://secret-internal-host:8001/v1','api_key':'never-expose-key','model':'served-model'}]))
    get_settings.cache_clear()
    values = [('complete', False, now - 10, 1000), ('error', False, now - 20, 2000),
              ('cancelled', False, now - 30, 300), ('running', False, now - 40, None),
              ('running', False, now - 500, None), ('complete', True, now - 60, 20),
              ('complete', False, now - 90000, 8000)]
    with client.db_factory() as db:
        for status, test, started, duration in values:
            db.add(Run(id=str(uuid4()), user_id=identity['user']['subject'], connection_id='demo' if test else 'office',
                provider='test' if test else 'vllm', model='demo' if test else 'served-model', is_test=test,
                status=status, started_at=started, deadline_at=started + 150, duration_ms=duration, output_chars=10))
        db.commit()
    result = client.get('/admin/overview', headers=headers(identity))
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload['users']['total'] == 2 and payload['users']['sessions'] == 2
    assert payload['requests_24h']['total'] == 6
    assert payload['requests_24h']['complete'] == 2 and payload['requests_24h']['interrupted'] == 1
    assert payload['requests_24h']['average_ms'] == 510 and payload['generating'] == 1
    assert len(payload['daily']) == 7 and sum(day['total'] for day in payload['daily']) == 7
    assert payload['service']['database'] == 'ready'
    assert all(value not in result.text for value in ['never-expose-key', 'secret-internal-host', 'base_url', 'jwt_secret'])
    live = client.get('/admin/overview?mode=live', headers=headers(identity)).json()
    assert live['requests_24h']['total'] == 5 and live['requests_24h']['success_rate'] == 33.3
    assert live['requests_24h']['average_ms'] == 1000 and live['users'] == payload['users']
    test = client.get('/admin/overview?mode=test', headers=headers(identity)).json()
    assert test['requests_24h']['total'] == 1 and test['requests_24h']['average_ms'] == 20
    rows = client.get('/admin/requests?status=interrupted', headers=headers(identity)).json()['items']
    assert len(rows) == 1 and rows[0]['status'] == 'interrupted'


def test_invalid_model_configuration_does_not_hide_other_operations_data(client, account, monkeypatch):
    identity = operator(client, account)
    monkeypatch.setenv('LLM_CONNECTIONS_JSON', '[{"api_key":"secret-invalid"}]')
    get_settings.cache_clear()
    result = client.get('/admin/overview', headers=headers(identity))
    assert result.status_code == 200 and result.json()['configuration_error']
    assert result.json()['users']['total'] == 1 and 'secret-invalid' not in result.text


def test_empty_metrics_are_not_fabricated_and_timezone_is_explicit(client, account):
    identity = operator(client, account)
    with client.db_factory() as db:
        db.get(User, identity['user']['subject']).time_zone = 'Asia/Seoul'
        db.commit()
    result = client.get('/admin/overview', headers=headers(identity)).json()
    assert result['requests_24h']['total'] == 0 and result['requests_24h']['average_ms'] is None
    assert result['requests_24h']['success_rate'] is None and result['first_record_at'] is None
    assert result['time_zone'] == 'Asia/Seoul' and len(result['daily']) == 7
    assert result['model_usage'] == []


def test_attempt_records_preserve_failure_regeneration_and_deletion_metadata(client, account, monkeypatch):
    identity = operator(client, account)
    access = headers(identity)
    conversation = client.post('/chat/conversations', headers=access, json={}).json()['id']
    request_id = str(uuid4())
    body = {'content':'PRIVATE-QUESTION-SENTINEL', 'request_id':request_id}
    response = client.post(f'/chat/conversations/{conversation}/messages', headers=access, json=body)
    assert 'event: done' in response.text
    with client.db_factory() as db:
        run = db.get(Run, request_id)
        assert run.status == 'complete' and run.first_token_ms is not None
        assert run.duration_ms >= run.first_token_ms and run.output_chars > 0
    assert client.post(f'/chat/conversations/{conversation}/messages', headers=access, json=body).status_code == 409
    async def fail(*args):
        yield 'PARTIAL-PRIVATE-ANSWER'
        raise providers.ProviderError('private-error-text')
    monkeypatch.setattr(providers, 'stream_reply', fail)
    retry_id = str(uuid4())
    result = client.post(f'/chat/conversations/{conversation}/messages', headers=access,
        json=body | {'request_id':retry_id,'retry':True})
    assert 'event: error' in result.text
    rows = client.get('/admin/requests', headers=access).json()['items']
    assert len(rows) == 2 and {row['status'] for row in rows} == {'complete','error'}
    broken = next(row for row in rows if row['id'] == retry_id)
    assert broken['output_chars'] == len('PARTIAL-PRIVATE-ANSWER') and broken['error_code'] == 'provider_error'
    assert client.delete(f'/chat/conversations/{conversation}', headers=access).status_code == 204
    with client.db_factory() as db:
        assert db.get(Run, request_id).conversation_id is None
    for path in ['/admin/requests','/admin/overview','/admin/audit']:
        payload = client.get(path, headers=access).text
        assert all(value not in payload for value in ['PRIVATE-QUESTION-SENTINEL','PARTIAL-PRIVATE-ANSWER','private-error-text','password_hash','details_json'])


def test_cancel_requested_at_stream_end_is_counted_as_cancelled(client, account, monkeypatch):
    identity = operator(client, account)
    access = headers(identity)
    conversation = client.post('/chat/conversations', headers=access, json={}).json()['id']
    async def reply(*args):
        yield '첫 조각'
        with client.db_factory() as db:
            db.get(Conversation, conversation).cancel_requested = True
            db.commit()
        yield '마지막 조각'
    monkeypatch.setattr(providers, 'stream_reply', reply)
    request_id = str(uuid4())
    response = client.post(f'/chat/conversations/{conversation}/messages', headers=access,
        json={'content':'취소 테스트','request_id':request_id})
    assert 'event: cancelled' in response.text
    with client.db_factory() as db:
        assert db.get(Run, request_id).status == 'cancelled'


def test_expired_generation_is_marked_interrupted_when_reclaimed(client, account):
    identity = account()
    conversation = client.post('/chat/conversations', headers=headers(identity), json={}).json()['id']
    connection = providers.Connection(id='demo',name='Demo',provider='test',model='demo')
    previous = TurnInput(content='이전 요청', request_id=uuid4())
    with client.db_factory() as db:
        prepare(db, UUID(conversation), identity['user']['subject'], previous, connection)
        db.get(Conversation, conversation).generation_until = int(time.time()) - 1
        db.get(Run, str(previous.request_id)).deadline_at = int(time.time()) - 1
        db.commit()
    with client.db_factory() as db:
        prepare(db, UUID(conversation), identity['user']['subject'], TurnInput(content='다음 요청',request_id=uuid4()), connection)
        previous_run = db.get(Run, str(previous.request_id))
        assert previous_run.status == 'error' and previous_run.error_code == 'interrupted'


def test_user_search_pagination_and_audit_redaction(client, account):
    identity = operator(client, account)
    account('searchable')
    access = headers(identity)
    assert len(client.get('/admin/users?q=search', headers=access).json()['items']) == 1
    assert client.get('/admin/users?q=%25', headers=access).json()['items'] == []
    page = client.get('/admin/users?limit=1', headers=access).json()
    other = client.get('/admin/users?limit=1&offset=1', headers=access).json()
    assert page['has_more'] and page['items'][0]['id'] != other['items'][0]['id']
    with client.db_factory() as db:
        db.add(AuditLog(action='admin.test',actor_user_id=identity['user']['subject'],target_user_id=identity['user']['subject'],
            details_json='{"secret":"never-show"}', ip_address='secret-ip'))
        db.commit()
    result = client.get('/admin/audit', headers=access)
    assert 'never-show' not in result.text and 'secret-ip' not in result.text
    record = next(row for row in result.json()['items'] if row['action']=='admin.test')
    assert record['actor'] == 'operator' and record['target'] == 'operator'
    assert client.get('/admin/users?limit=1000',headers=access).status_code == 422
    assert client.get('/admin/overview?mode=unknown',headers=access).status_code == 422


def test_local_admin_tool_uses_existing_users_audits_and_protects_last_operator(client, account):
    identity, second = account('firstadmin'), account('secondadmin')
    database_url = str(client.db_factory.kw['bind'].url)
    environment = os.environ | {'DATABASE_URL':database_url,'APP_ENV':'test'}
    def command(action, username):
        return subprocess.run([sys.executable,'manage_admin.py',action,username],env=environment,
            cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True)
    assert command('grant','missing').returncode != 0
    assert command('grant','firstadmin').returncode == 0
    assert client.get('/admin/overview',headers=headers(identity)).status_code == 200
    assert command('revoke','firstadmin').returncode != 0
    assert command('grant','secondadmin').returncode == 0
    assert command('revoke','firstadmin').returncode == 0
    assert client.get('/admin/overview',headers=headers(identity)).status_code == 403
    assert client.get('/admin/overview',headers=headers(second)).status_code == 200
    actions = client.get('/admin/audit',headers=headers(second)).json()['items']
    assert len([row for row in actions if row['action'].startswith('admin.role.')]) == 3


def test_custom_title_survives_first_message_and_rename_enforces_boundaries(client, account):
    identity = account()
    access = headers(identity)
    conversation = client.post('/chat/conversations',headers=access,json={}).json()['id']
    path = f'/chat/conversations/{conversation}'
    assert client.patch(path,headers=access,json={'title':'  내 프로젝트  '}).json()['title'] == '내 프로젝트'
    assert client.patch(path,headers=access,json={'title':'   '}).status_code == 422
    assert client.patch(path,headers=access,json={'title':'x'*121}).status_code == 422
    assert client.patch(path,headers=access,json={'title':'new','user_id':'other'}).status_code == 422
    assert client.post(path+'/messages',headers=access,json={'content':'자동 제목이 덮어쓰면 안 됩니다','request_id':str(uuid4())}).status_code == 200
    assert client.get(path,headers=access).json()['conversation']['title'] == '내 프로젝트'
