from io import BytesIO
import json
from uuid import UUID, uuid4

from fastapi import HTTPException
import httpx
from openpyxl import Workbook, load_workbook
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.modules.chat import providers
from app.modules.chat.independent import RequestCancelled
from app.modules.chat.router import TurnInput, prepare
from app.modules.knowledge.models import Document
from app.modules.workflows import artifacts, table_analysis
from app.modules.workflows.models import Artifact
from test_chat import auth, create, events, send
from test_knowledge import base, index, upload
from test_parallel_and_gateway import use_model


@pytest.fixture
def workspace(client, account):
    identity = account()
    headers, user_id = auth(identity), identity['user']['subject']
    conversation = create(client, headers)
    with client.db_factory() as db:
        turn = prepare(db, UUID(conversation), user_id, TurnInput(content='표를 분석해 줘', request_id=uuid4()),
                       providers.Connection(id='demo', name='테스트', provider='test', model='demo'))
        bind = db.get_bind()

    def attach(name, contents, *, location=conversation):
        doc = upload(client, headers, conversation=location, filename=name,
                     raw=contents if isinstance(contents, bytes) else contents.encode())
        index(client)
        return {'document_id': doc['id']}

    return bind, user_id, conversation, turn, attach


def test_join_exact_monthly_totals_writes_valid_spreadsheet_and_inert_chart(workspace, client, account):
    bind, user_id, conversation, turn, attach = workspace
    sales = attach('매출.csv', '\ufeff계정,날짜,매출\nA,2026-10-01,"1,000.10"\nA,2026-10-07,-0.20\n'
                   'B,2026-11-03,0\nC,2026-11-13,4\nA,2026-10-23,\n\n')
    teams = attach('팀.tsv', 'id\t팀\nA\t<동부>\nB\t서부\nD\t중부\n')
    options = {'document_ids': [sales['document_id'], teams['document_id']]}
    result, files = table_analysis.execute(bind, user_id, turn, options, {
        'action': 'aggregate', 'source': sales,
        'join': {'right': teams, 'left_on': '계정', 'right_on': 'id', 'kind': 'left'},
        'group_by': ['right.팀'], 'month_of': '날짜', 'output': 'both', 'chart_metric': '순매출',
        'title': '<팀별 순매출>',
        'metrics': [{'operation': 'sum', 'column': '매출', 'label': '순매출'}, {'operation': 'count'},
                    {'operation': 'count', 'column': '매출'}, {'operation': 'average', 'column': '매출'}],
    })
    assert result['columns'] == ['right.팀', '날짜 (월)', '순매출', 'count', 'count(매출)', 'average(매출)']
    assert result['rows'] == [
        ['<동부>', '2026-10', '999.9', 3, 2, '499.95'],
        ['서부', '2026-11', '0', 1, 1, '0'],
        [None, '2026-11', '4', 1, 1, '4'],
    ]
    assert result['coverage']['combine'] == {'kind': 'left', 'rows': 5, 'matched_left_rows': 4,
        'unmatched_left_rows': 1, 'unmatched_right_rows': 1, 'left_on': '계정', 'right_on': 'id'}
    assert result['coverage']['metric_values'][0] == {
        'label': '순매출', 'operation': 'sum', 'column': '매출', 'used_rows': 4, 'missing_rows': 1,
    }
    assert result['coverage']['inputs'] == [{'rows': 5, 'blank_rows': 1}, {'rows': 3, 'blank_rows': 0}]
    assert len(files) == 2 and result['files'] == files and result['chart_display_rows'] == 3

    outsider = auth(account('outsider'))
    with client.db_factory() as db:
        workbook, chart = [db.get(Artifact, entry['id']) for entry in files]
        assert workbook.status == chart.status == 'draft'
        assert json.loads(workbook.payload_json)['analysis']['documents'] == result['documents']
        output = load_workbook(BytesIO(workbook.data), read_only=True)
        try:
            assert list(output.active.values)[1] == ('<동부>', '2026-10', 999.9, 3, 2, 499.95)
        finally:
            output.close()
        assert b'<script' not in chart.data and b'&lt;' in chart.data and b'999.9' in chart.data
    table_analysis.verify(bind, user_id, conversation, result['documents'])
    artifacts.finalize(bind, user_id, turn.request_id, [file['id'] for file in files], approve=True)
    for file in files:
        assert client.get(f'/workflows/artifacts/{file["id"]}/download', headers=outsider).status_code == 404


def test_append_preserves_precision_and_rejects_ambiguous_join(workspace, client):
    bind, user_id, _, turn, attach = workspace
    first = attach('one.csv', 'ID,amount\n1,123456789012345678901.01\n2,0.10\n')
    second = attach('two.tsv', 'amount\tID\n0.20\t1\n0.01\t1\n')
    options = {'document_ids': [first['document_id'], second['document_id']]}
    base = {'action': 'aggregate', 'source': first,
            'metrics': [{'operation': 'sum', 'column': 'amount'}, {'operation': 'count'}]}
    with pytest.raises(HTTPException, match='중복 키'):
        table_analysis.execute(bind, user_id, turn, options, base | {
            'join': {'right': second, 'left_on': 'ID', 'right_on': 'ID'},
        })
    result, files = table_analysis.execute(bind, user_id, turn, options, base | {'append': second, 'output': 'xlsx'})
    assert result['rows'] == [['123456789012345678901.32', 4]]
    with client.db_factory() as db:
        workbook = db.get(Artifact, files[0]['id'])
        output = load_workbook(BytesIO(workbook.data), read_only=True)
        try:
            assert list(output.active.values)[1] == ('123456789012345678901.32', 4)
        finally:
            output.close()


def test_inspect_and_aggregate_excel_read_cached_values_without_running_formulas(workspace):
    bind, user_id, _, turn, attach = workspace
    buffer = BytesIO()
    book = Workbook()
    sheet = book.active
    sheet.title = '매출'
    sheet.append(['날짜', '순매출'])
    sheet.append(['2026-01-10', 10])
    sheet.append([None, None])
    sheet.append(['2026-01-16', '=1+10'])
    sheet.append([None, '=5+5'])
    sheet.cell(row=2, column=500).number_format = '0.00'
    other = book.create_sheet('메모')
    other.append(['번호', '요약'])
    other.append([1, '참고'])
    book.save(buffer)
    book.close()
    source = attach('파일.xlsx', buffer.getvalue())
    options = {'document_ids': [source['document_id']]}
    inspected, _ = table_analysis.execute(bind, user_id, turn, options, {'action': 'inspect', 'source': source})
    assert inspected['sheets'] == ['매출', '메모'] and inspected['sheet_required']
    request = {'action': 'aggregate', 'source': source, 'month_of': '날짜',
               'metrics': [{'operation': 'sum', 'column': '순매출'}, {'operation': 'count'}]}
    with pytest.raises(HTTPException, match='시트 이름'):
        table_analysis.execute(bind, user_id, turn, options, request)
    request['source'] |= {'sheet': '매출'}
    result, _ = table_analysis.execute(bind, user_id, turn, options, request)
    assert result['rows'] == [['2026-01', '10', 2], [None, None, 1]]
    assert result['documents'][0]['formula_policy'] == 'cached_values_only'
    assert result['documents'][0]['uncomputed_formula_cells'] == {'순매출': 2}
    assert result['coverage']['inputs'][0] == {'rows': 3, 'blank_rows': 1}
    assert result['coverage']['metric_values'][0]['missing_rows'] == 2


def test_analysis_rejects_unselected_foreign_expired_and_changed_documents(workspace, client, account):
    bind, user_id, conversation, turn, attach = workspace
    source = attach('표.csv', 'a,b\n1,2\n')
    options = {'document_ids': [source['document_id']]}
    request = {'action': 'inspect', 'source': source}
    result, _ = table_analysis.execute(bind, user_id, turn, options, request)
    with pytest.raises(HTTPException) as absent:
        table_analysis.execute(bind, user_id, turn, {}, request)
    assert absent.value.status_code == 404
    bob = account('bob')
    another_conversation = create(client, auth(bob))
    with pytest.raises(HTTPException) as blocked:
        table_analysis.catalog(bind, bob['user']['subject'], another_conversation, options)
    assert blocked.value.status_code == 404
    with client.db_factory() as db:
        db.get(Document, source['document_id']).revision += 1
        db.commit()
    with pytest.raises(HTTPException) as changed:
        table_analysis.verify(bind, user_id, conversation, result['documents'])
    assert changed.value.status_code == 409
    with client.db_factory() as db:
        db.get(Document, source['document_id']).expires_at = 1
        db.commit()
    with pytest.raises(HTTPException) as expired:
        table_analysis.execute(bind, user_id, turn, options, request)
    assert expired.value.status_code == 404


def test_automatic_catalog_honors_current_knowledge_access(workspace, client, account):
    bind, user_id, conversation, turn, _ = workspace
    owner = auth(account('owner'))
    base_id = base(client, owner)
    table = upload(client, owner, base_id=base_id, filename='공유.csv', text='항목,수치\nA,2\n')
    index(client)
    options = {'_auto_scope': True}
    assert table_analysis.catalog(bind, user_id, conversation, options) == []
    assert client.post(f'/knowledge/bases/{base_id}/members', headers=owner, json={'username': 'alice'}).status_code == 200
    assert table_analysis.catalog(bind, user_id, conversation, options) == [
        {'document_id': table['id'], 'filename': table['filename']},
    ]
    result, _ = table_analysis.execute(bind, user_id, turn, options, {
        'action': 'aggregate', 'source': {'document_id': table['id']},
        'metrics': [{'operation': 'sum', 'column': '수치'}],
    })
    assert result['rows'] == [['2']]
    assert client.delete(f'/knowledge/bases/{base_id}/members/{user_id}', headers=owner).status_code == 200
    with pytest.raises(HTTPException) as denied:
        table_analysis.verify(bind, user_id, conversation, result['documents'])
    assert denied.value.status_code == 404


def test_analysis_fails_closed_for_bad_data_limits_or_cancellation(workspace, client, monkeypatch):
    bind, user_id, _, turn, attach = workspace
    source = attach('숫자.csv', '단위,금액\nA,12\nB,2원\n')
    options = {'document_ids': [source['document_id']]}
    request = {'action': 'aggregate', 'source': source, 'output': 'both',
               'metrics': [{'operation': 'sum', 'column': '금액'}]}
    with pytest.raises(HTTPException, match='유효하지 않거나'):
        table_analysis.execute(bind, user_id, turn, options, request)
    with pytest.raises(RequestCancelled):
        table_analysis.execute(bind, user_id, turn, options, request, cancelled=lambda: True)
    monkeypatch.setattr(table_analysis, 'MAX_ROWS', 1)
    with pytest.raises(HTTPException, match='최대 2행'):
        table_analysis.execute(bind, user_id, turn, options, {'action': 'inspect', 'source': source})
    with client.db_factory() as db:
        assert db.scalars(select(Artifact)).all() == []


def test_multiple_outputs_roll_back_if_second_file_exceeds_run_quota(workspace, client):
    bind, user_id, _, turn, attach = workspace
    source = attach('수치.csv', '항목,수치\nA,5\n')
    previous = [artifacts.create(bind, user_id, turn.assistant_id, turn.request_id,
        {'name': f'기존 {number}', 'format': 'txt', 'content': '검증'}) for number in range(2)]
    with pytest.raises(HTTPException, match='결과 파일은 3개'):
        table_analysis.execute(bind, user_id, turn, {'document_ids': [source['document_id']]}, {
            'action': 'aggregate', 'source': source, 'output': 'both',
            'metrics': [{'operation': 'sum', 'column': '수치'}],
        })
    with client.db_factory() as db:
        assert set(db.scalars(select(Artifact.id))) == {entry['id'] for entry in previous}


def test_chat_can_join_files_calculate_all_rows_and_publish_two_artifacts(client, account, monkeypatch):
    monkeypatch.setenv('LLM_CONNECTIONS_JSON', json.dumps([{
        'id': 'data', 'name': 'Data Model', 'provider': 'vllm', 'base_url': 'http://model.test/v1',
        'model': 'tabular', 'supports_tools': True, 'context_window': 24576,
    }]))
    get_settings.cache_clear()
    owner = auth(account())
    conversation = create(client, owner)
    sales = upload(client, owner, conversation=conversation, filename='sales.csv',
                   text='code,date,value\n' + ''.join(f'1,2026-11-{i % 28 + 1:02d},0.1\n' for i in range(120)))
    index(client)
    teams = upload(client, owner, conversation=conversation, filename='teams.tsv', text='code\tteam\n1\t북부\n')
    index(client)
    analysis = {
        'action': 'aggregate', 'source': {'document_id': sales['id']},
        'join': {'right': {'document_id': teams['id']}, 'left_on': 'code', 'right_on': 'code'},
        'group_by': ['right.team'], 'month_of': 'date', 'output': 'both',
        'metrics': [{'operation': 'sum', 'column': 'value'}, {'operation': 'count'}],
    }
    stages = []

    def upstream(request):
        payload = json.loads(request.content)
        schema = payload.get('response_format', {}).get('json_schema', {}).get('schema', {})
        if 'route' in schema.get('properties', {}):
            stages.append('plan')
            message = {'content': json.dumps({'route': 'direct', 'analyze_table': True})}
        elif 'verdict' in schema.get('properties', {}):
            stages.append('review')
            message = {'content': json.dumps({'verdict': 'supported'})}
        else:
            tools = {tool['function']['name'] for tool in payload['tools']}
            assert table_analysis.TOOL_NAME in tools
            result = next((item for item in payload['messages'] if item.get('tool_name') == table_analysis.TOOL_NAME), None)
            if result:
                stages.append('answer')
                value = json.loads(result['content'])
                assert value['rows'] == [['북부', '2026-11', '12', 120]]
                assert value['coverage']['combine']['matched_left_rows'] == 120
                message = {'content': '북부 2026년 11월 매출 합계는 12이며 120행을 검산했습니다. [TA1]'}
            else:
                stages.append('call')
                message = {'content': '', 'tool_calls': [{'id': 'table', 'type': 'function', 'function': {
                    'name': table_analysis.TOOL_NAME, 'arguments': json.dumps(analysis),
                }}]}
        return httpx.Response(200, json={'choices': [{'message': message}]})

    use_model(monkeypatch, upstream)
    response = send(client, owner, conversation, '두 표를 조인하고 월별 매출 합계를 계산해서 차트와 엑셀을 줘.',
                    connection_id='data', model='tabular', assistant={'document_ids': [sales['id'], teams['id']]})
    assert response.status_code == 200, response.text
    assert events(response)[-1][0] == 'done', response.text[-6000:]
    assert stages.index('call') < stages.index('answer')
    answer = client.get(f'/chat/conversations/{conversation}', headers=owner).json()['messages'][-1]
    assert '[TA1]' in answer['content']
    source, = [source for source in answer['sources'] if source['id'] == 'TA1']
    assert source['app'] == 'table_analysis' and [item['document_id'] for item in source['documents']] == [sales['id'], teams['id']]
    assert {file['format'] for file in answer['artifacts']} == {'html', 'xlsx'}
    for file in answer['artifacts']:
        download = client.get(f'/workflows/artifacts/{file["id"]}/download', headers=owner)
        assert download.status_code == 200
        assert download.content.startswith(b'PK' if file['format'] == 'xlsx' else b'<!doctype html>')
        preview = client.get(f'/workflows/artifacts/{file["id"]}', headers=owner)
        assert preview.status_code == 200 and preview.json()['kind'] == 'table_analysis'
