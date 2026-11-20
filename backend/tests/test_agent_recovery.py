"""일시적인 읽기 실패와 불확실한 쓰기 결과를 구분하는 복구 계약."""
import asyncio
import json

from fastapi import HTTPException
import pytest

from app.modules.assistant import gitlab, recovery, routing
from app.modules.assistant.engine import AnalysisCancelled, trim_result
from app.modules.chat import trace
from test_reasoning_trace_provider import recorder


def run(operation, *, readonly=True, cancelled=lambda: False):
    records = []
    def record(name, status, detail):
        records.append((name,status,detail))
        return {'name':name,'status':status,'detail':detail,'step':1}
    result = asyncio.run(recovery.call(operation,name='lookup',readonly=readonly,cancelled=cancelled,record=record))
    return result,records


def test_readonly_transient_failure_retries_once_and_reports_actual_outcome():
    calls = []
    async def operation():
        calls.append(True)
        if len(calls)==1:raise HTTPException(502,'temporarily unavailable')
        return {'actual':'source'}
    result,records=run(operation)
    assert len(calls)==2 and result=={'actual':'source'}
    assert [item[1] for item in records]==['running','complete']


@pytest.mark.parametrize('status',[401,403,404,409,422])
def test_auth_scope_conflict_and_input_failures_never_retry(status):
    calls=[]
    async def operation():
        calls.append(True)
        raise HTTPException(status,'rejected')
    with pytest.raises(HTTPException) as error:run(operation)
    assert error.value.status_code==status and len(calls)==1


def test_remote_writes_are_not_replayed_after_lost_response():
    remote_effects=[]
    async def operation():
        remote_effects.append('created')
        raise HTTPException(502,'response lost')
    with pytest.raises(HTTPException):run(operation,readonly=False)
    assert remote_effects==['created']


def test_readonly_retries_are_bounded_and_stop_obeys_cancellation():
    calls=[]
    async def operation():
        calls.append(True)
        raise TimeoutError()
    with pytest.raises(TimeoutError):run(operation)
    assert len(calls)==2
    calls.clear()
    with pytest.raises(AnalysisCancelled):run(operation,cancelled=lambda:bool(calls))
    assert len(calls)==1


def test_tool_result_redaction_keeps_valid_json_and_masks_nested_secrets():
    raw={'result':[{'access_token':'private-value','content':'Authorization: Bearer token-secret\nAPI_KEY=abc123'}],
        'path':'src/login.py','count':3}
    encoded=trim_result(raw)
    parsed=json.loads(encoded)
    assert parsed['path']=='src/login.py' and parsed['count']==3
    assert 'private-value' not in encoded and 'token-secret' not in encoded and 'abc123' not in encoded
    assert raw['result'][0]['access_token']=='private-value'


def test_unknown_tool_credentials_and_quoted_values_with_spaces_are_redacted():
    encoded=trim_result({'token':'OPAQUE_SENTINEL','secret':'SECRET_SENTINEL','private_key':'PRIVATE_SENTINEL',
        'headers':{'authorization':'Bearer AUTH_SENTINEL'},'note':'CI_JOB_TOKEN=CI_SENTINEL\npassword="two words secret"'})
    assert not any(secret in encoded for secret in ['OPAQUE_SENTINEL','SECRET_SENTINEL','PRIVATE_SENTINEL','AUTH_SENTINEL','CI_SENTINEL','two words secret'])
    assert json.loads(encoded)['headers']['authorization']=='[비밀값 숨김]'


def test_camelcase_and_structured_credentials_are_redacted_as_whole_values():
    value={'credentials':['OPAQUE_ONE','OPAQUE_TWO'],'privateKey':{'d':'RSA_PRIVATE'},
        'accessToken':'ACCESS_VALUE','clientSecret':'CLIENT_VALUE','note':'refreshToken=REFRESH_VALUE'}
    encoded=trim_result(value)
    assert not any(secret in encoded for secret in ['OPAQUE_ONE','OPAQUE_TWO','RSA_PRIVATE','ACCESS_VALUE','CLIENT_VALUE','REFRESH_VALUE'])
    assert json.loads(encoded)['credentials']=='[비밀값 숨김]'


def test_parallel_stage_ids_follow_worker_even_when_completion_is_reversed(recorder,client):
    starts={name:recorder.activity('parallel_worker','running',name,1,correlation=name) for name in ['A','B','C']}
    ends={name:recorder.activity('parallel_worker','complete',name,1,correlation=name) for name in ['C','A','B']}
    assert all(starts[name]['id']==ends[name]['id'] for name in starts)
    recorder.finish()
    with client.db_factory() as db:
        saved=trace.snapshot_for_message(db,recorder.message_id)
        assert [(item['detail'],item['status']) for item in saved['steps']]==[('A','complete'),('B','complete'),('C','complete')]


def test_recovered_interrupted_trace_preserves_text_without_inventing_runtime(recorder,client):
    running=recorder.activity('terminal_exec','running','검증 명령',1)
    recorder.reasoning='마지막으로 저장된 공개 추론'
    recorder.flush(force=True)
    with client.db_factory() as db:
        trace.mark_interrupted(db,recorder.message_id)
        db.commit()
    with client.db_factory() as db:
        saved=trace.snapshot_for_message(db,recorder.message_id)
        assert saved['status']=='error' and saved['reasoning']=='마지막으로 저장된 공개 추론'
        assert saved['steps'][0]['id']==running['id'] and saved['steps'][0]['status']=='error'
        assert saved['steps'][0]['duration_ms'] is None
        assert '확인하지 못했습니다' in saved['steps'][0]['detail']


def test_simple_chat_fast_path_does_not_accept_mixed_tool_requests():
    assert routing.simple_chat('안녕하세요!')
    assert not routing.simple_chat('안녕하세요! @gitlab team/service 분석해줘')
    assert not routing.simple_chat('안녕. 회사 휴가 규정 알려줘')


def test_explicit_build_request_is_authority_but_repository_quotes_are_not():
    request=gitlab.prompt('@gitlab team/service 빌드하고 테스트해줘')
    assert request.execute and request.edit and not request.publication
    assert not gitlab.prompt('@gitlab team/service\n> 빌드해줘').execute
