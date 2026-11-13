from io import BytesIO
import json
from uuid import UUID, uuid4
from zipfile import ZipFile

from fastapi import HTTPException
from openpyxl import load_workbook
from pptx import Presentation
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.modules.assistant import routing
from app.modules.auth.models import User
from app.modules.chat import providers, scheduler
from app.modules.chat.models import Conversation, GenerationRun, Message
from app.modules.chat.router import TurnInput, prepare
from app.modules.workflows import archives, artifact_revision, artifacts
from app.modules.workflows.conversations import capture
from app.modules.workflows.models import Artifact, MessageState
from test_chat import auth, create, events, send


@pytest.fixture
def document_workspace(client, account):
    headers = auth(account())
    with client.db_factory() as db:
        user_id = db.scalar(select(User.id).where(User.username == 'alice'))
        bind = db.get_bind()
    return headers, user_id, bind, create(client, headers)


def pending_turn(client, user_id, conversation):
    with client.db_factory() as db:
        connection = providers.resolve(get_settings(), 'demo')
        return prepare(db, UUID(conversation), user_id,
            TurnInput(content='결과 파일을 확인해 줘', request_id=uuid4()), connection)


def complete_turn(client, turn):
    with client.db_factory() as db:
        row = db.get(Conversation, turn.conversation_id)
        row.generation_id, row.generation_until = None, 0
        scheduler.finish(db, turn.request_id, 'complete')
        db.get(GenerationRun, turn.request_id).status = 'complete'
        message = db.get(Message, turn.assistant_id)
        message.status, message.content = 'complete', '다운로드할 수 있는 파일입니다.'
        capture(db, message, turn.request_id)
        db.commit()


def make_result(client, user_id, conversation, data):
    turn = pending_turn(client, user_id, conversation)
    with client.db_factory() as db:
        bind = db.get_bind()
    file = artifacts.create(bind, user_id, turn.assistant_id, turn.request_id, data)
    artifacts.finalize(bind, user_id, turn.request_id, [file['id']], approve=True)
    complete_turn(client, turn)
    return file, turn.assistant_id


def original_deck():
    return {'format': 'pptx', 'name': '분기 보고서', 'slides': [
        {'title': '현황', 'bullets': ['첫 번째 슬라이드'], 'notes': '1번 발표자 메모'},
        {'title': '계획', 'bullets': ['두 번째 슬라이드']},
        {'title': '일정', 'bullets': ['세 번째 슬라이드'], 'notes': '3번 발표자 메모'},
    ]}


@pytest.mark.parametrize(('question', 'expected'), [
    ('아까 만든 PPTX에서 3번 슬라이드만 고쳐 줘', True),
    ('아까 만든 파일 제목을 바꿔 줘', True),
    ('아까 만든 파일 제목을 수정하지 말아 줘', False),
    ('파일 수정 방법을 알려 줘', False),
    ('“파일을 수정해 줘”를 영어로 바꿔 줘', False),
    ('첨부 문서를 읽어 PDF로 만들어 줘', False),
])
def test_direct_revision_hint_avoids_unrelated_file_requests(question, expected):
    assert artifact_revision.clear_revision_request(question) is expected


def deck_contents(data):
    return [([shape.text for shape in slide.shapes],
             slide.notes_slide.notes_text_frame.text if slide.has_notes_slide else '')
            for slide in Presentation(BytesIO(data)).slides]


def test_revises_one_real_slide_and_keeps_original(document_workspace, client):
    headers, user_id, bind, conversation = document_workspace
    previous, _ = make_result(client, user_id, conversation, original_deck())
    turn = pending_turn(client, user_id, conversation)
    assert artifact_revision.requested('아까 만든 PPTX에서 3번 슬라이드만 고쳐 줘')
    assert artifact_revision.catalog(bind, user_id, conversation, before_message_id=turn.assistant_id) == [
        {key: previous[key] for key in ('id', 'name', 'format')}
    ]
    read, file = artifact_revision.execute(bind, user_id, turn, artifact_revision.READ_TOOL,
        {'name': '분기 보고서', 'part': 'slide', 'index': 3})
    assert file is None and read['id'] == previous['id']
    assert read['title'] == '일정' and read['items'] == ['세 번째 슬라이드']
    original = client.get(f"/workflows/artifacts/{previous['id']}/download", headers=headers).content
    result, revised = artifact_revision.execute(bind, user_id, turn, artifact_revision.REVISE_TOOL,
        {'artifact_id': previous['id'], 'slide': {'number': 3, 'title': '새 일정'}})
    assert result['revised_file'] == revised
    assert result['original']['id'] == previous['id']
    assert revised['id'] != previous['id'] and revised['name'] == '분기 보고서 - 수정.pptx'
    path = f"/workflows/artifacts/{revised['id']}/download"
    assert client.get(path, headers=headers).status_code == 404
    artifacts.finalize(bind, user_id, turn.request_id, [revised['id']], approve=True)
    response = client.get(path, headers=headers)
    assert response.status_code == 200
    assert deck_contents(response.content)[:2] == deck_contents(original)[:2]
    assert deck_contents(response.content)[2] == (['새 일정', '•  세 번째 슬라이드'], '3번 발표자 메모')
    assert client.get(f"/workflows/artifacts/{previous['id']}/download", headers=headers).content == original


def test_chat_followup_reads_and_revises_existing_pptx(document_workspace, client, monkeypatch):
    headers, user_id, _, conversation = document_workspace
    previous, _ = make_result(client, user_id, conversation, original_deck())
    monkeypatch.setenv('LLM_CONNECTIONS_JSON', json.dumps([{
        'id': 'local', 'name': 'Local', 'provider': 'vllm', 'base_url': 'http://chat-server.test/v1',
        'model': 'local-chat', 'supports_tools': True,
    }]))
    get_settings.cache_clear()
    dispatched = []

    async def decide(*_):
        return routing.Plan(route='direct', revise_file=True)

    async def model(connection, model_name, messages, tools, settings):
        names = {tool['function']['name'] for tool in tools}
        assert artifact_revision.TOOL_NAMES <= names
        previous_step = messages[-1]
        if previous_step.get('tool_name') == artifact_revision.REVISE_TOOL:
            assert json.loads(previous_step['content'])['original']['id'] == previous['id']
            return {'role': 'assistant', 'content': '3번 슬라이드를 새 파일에 반영했습니다.', 'tool_calls': []}
        if previous_step.get('tool_name') == artifact_revision.READ_TOOL:
            read = json.loads(previous_step['content'])
            assert read['title'] == '일정' and read['items'] == ['세 번째 슬라이드']
            name = artifact_revision.REVISE_TOOL
            args = {'artifact_id': read['id'], 'slide': {'number': 3, 'title': '새 일정'}}
        else:
            name = artifact_revision.READ_TOOL
            args = {'part': 'slide', 'index': 3}
        dispatched.append(name)
        return {'role': 'assistant', 'content': '', 'tool_calls': [{
            'id': str(len(dispatched)), 'type': 'function', 'function': {'name': name, 'arguments': args},
        }]}

    monkeypatch.setattr(routing, 'decide', decide)
    monkeypatch.setattr(providers, 'tool_reply', model)
    response = send(client, headers, conversation, '아까 만든 PPTX에서 3번 슬라이드만 고쳐 줘', connection_id='local')
    stream = events(response)
    assert stream[-1][0] == 'done', response.text
    assert dispatched == [artifact_revision.READ_TOOL, artifact_revision.REVISE_TOOL]
    file, = stream[-1][1]['message']['artifacts']
    assert file['name'] == '분기 보고서 - 수정.pptx' and file['id'] != previous['id']
    download = client.get(f"/workflows/artifacts/{file['id']}/download", headers=headers)
    assert download.status_code == 200 and deck_contents(download.content)[2][0][0] == '새 일정'


def test_revision_visibility_follows_active_files_and_forks(document_workspace, client, account):
    headers, user_id, bind, conversation = document_workspace
    previous, message_id = make_result(client, user_id, conversation, original_deck())
    other = create(client, headers)
    turn = pending_turn(client, user_id, other)
    with pytest.raises(HTTPException) as error:
        artifact_revision.execute(bind, user_id, turn, artifact_revision.READ_TOOL, {'artifact_id': previous['id']})
    assert error.value.status_code == 404
    complete_turn(client, turn)
    stranger = auth(account('bob'))
    with client.db_factory() as db:
        stranger_id = db.scalar(select(User.id).where(User.username == 'bob'))
        state = db.get(MessageState, message_id)
        state.artifact_ids_json = '[]'
        db.commit()
    assert not artifact_revision.catalog(bind, user_id, conversation)
    with client.db_factory() as db:
        db.get(MessageState, message_id).artifact_ids_json = json.dumps([previous['id']])
        db.commit()
    assert not artifact_revision.catalog(bind, stranger_id, conversation)
    assert client.get(f"/workflows/artifacts/{previous['id']}/download", headers=stranger).status_code == 404
    fork = client.post(f'/workflows/conversations/{conversation}/fork', headers=headers, json={'message_id': message_id})
    assert fork.status_code == 201
    branch = fork.json()['conversation']['id']
    assert client.delete(f'/chat/conversations/{conversation}', headers=headers).status_code == 204
    assert artifact_revision.catalog(bind, user_id, branch)[0]['id'] == previous['id']
    turn = pending_turn(client, user_id, branch)
    read, _ = artifact_revision.execute(bind, user_id, turn, artifact_revision.READ_TOOL,
        {'artifact_id': previous['id'], 'part': 'notes', 'index': 3})
    assert read['content'] == '3번 발표자 메모'


def test_edits_an_exact_text_span_and_table_cell(document_workspace, client):
    headers, user_id, bind, conversation = document_workspace
    previous, _ = make_result(client, user_id, conversation,
        {'format': 'xlsx', 'name': '월별 현황', 'columns': ['월', '실적'], 'rows': [['1월', 7], ['2월', 8]]})
    turn = pending_turn(client, user_id, conversation)
    _, revised = artifact_revision.execute(bind, user_id, turn, artifact_revision.REVISE_TOOL, {
        'artifact_id': previous['id'], 'cells': [{'row': 3, 'column': 2, 'value': '=1+1'}],
    })
    with client.db_factory() as db:
        book = load_workbook(BytesIO(db.get(Artifact, revised['id']).data), read_only=True)
        assert list(book.active.values) == [('월', '실적'), ('1월', 7), ('2월', "'=1+1")]
        book.close()
    artifacts.finalize(bind, user_id, turn.request_id, [], approve=False)
    with client.db_factory() as db:
        assert db.get(Artifact, revised['id']) is None
    complete_turn(client, turn)
    previous, _ = make_result(client, user_id, conversation,
        {'format': 'md', 'name': '현황', 'content': '앞 문단\n\n원래 결론\n\n마지막 문단'})
    turn = pending_turn(client, user_id, conversation)
    _, revised = artifact_revision.execute(bind, user_id, turn, artifact_revision.REVISE_TOOL, {
        'artifact_id': previous['id'], 'text': {'find': '원래 결론', 'replace': '새 결론'},
    })
    with client.db_factory() as db:
        assert db.get(Artifact, revised['id']).data.decode() == '앞 문단\n\n새 결론\n\n마지막 문단'
    artifacts.finalize(bind, user_id, turn.request_id, [], approve=False)
    assert client.get(f"/workflows/artifacts/{revised['id']}/download", headers=headers).status_code == 404


def test_rename_preserves_binary_and_rechecks_original_at_save(document_workspace, client, monkeypatch):
    headers, user_id, bind, conversation = document_workspace
    previous, _ = make_result(client, user_id, conversation, original_deck())
    turn = pending_turn(client, user_id, conversation)
    _, renamed = artifact_revision.execute(bind, user_id, turn, artifact_revision.REVISE_TOOL,
        {'artifact_id': previous['id'], 'new_name': '새 파일명'})
    assert renamed['name'] == '새 파일명.pptx' and renamed['sha256'] == previous['sha256']

    render = artifacts.render
    def delete_before_save(data):
        assert client.delete(f"/workflows/artifacts/{previous['id']}", headers=headers).status_code == 200
        return render(data)

    monkeypatch.setattr(artifacts, 'render', delete_before_save)
    with pytest.raises(HTTPException) as error:
        artifact_revision.execute(bind, user_id, turn, artifact_revision.REVISE_TOOL,
            {'artifact_id': previous['id'], 'slide': {'number': 2, 'title': '새 계획'}})
    assert error.value.status_code == 409
    with client.db_factory() as db:
        assert list(db.scalars(select(Artifact.id))) == [renamed['id']]


def test_revision_approval_rechecks_original_visibility(document_workspace, client):
    headers, user_id, bind, conversation = document_workspace
    previous, message_id = make_result(client, user_id, conversation, original_deck())
    turn = pending_turn(client, user_id, conversation)
    _, revised = artifact_revision.execute(bind, user_id, turn, artifact_revision.REVISE_TOOL,
        {'artifact_id': previous['id'], 'slide': {'number': 2, 'title': '새 계획'}})
    bundle = archives.create(bind, user_id, turn.assistant_id, turn.request_id,
        {'name': '수정본 묶음', 'file_ids': [revised['id']]})
    with client.db_factory() as db:
        with ZipFile(BytesIO(db.get(Artifact, bundle['id']).data)) as archive:
            assert deck_contents(archive.read(revised['name']))[1][0][0] == '새 계획'
        db.get(MessageState, message_id).artifact_ids_json = '[]'
        db.commit()
    with pytest.raises(HTTPException) as error:
        artifacts.finalize(bind, user_id, turn.request_id, [bundle['id']], approve=True)
    assert error.value.status_code == 409
    assert client.get(f"/workflows/artifacts/{revised['id']}/download", headers=headers).status_code == 404
    artifacts.finalize(bind, user_id, turn.request_id, [], approve=False)
    with client.db_factory() as db:
        assert db.get(Artifact, revised['id']) is None
        assert db.get(Artifact, bundle['id']) is None


@pytest.mark.parametrize('edit', [
    {'slide': {'number': 4, 'title': '없는 슬라이드'}},
    {'slide': {'number': 2, 'title': '계획'}},
    {'slide': {'number': 2}},
    {'text': {'find': '없는 텍스트', 'replace': '결론'}},
    {'new_name': '분기 보고서.pptx'},
])
def test_invalid_or_unchanged_revisions_do_not_create_files(document_workspace, client, edit):
    _, user_id, bind, conversation = document_workspace
    previous, _ = make_result(client, user_id, conversation, original_deck())
    turn = pending_turn(client, user_id, conversation)
    with pytest.raises(HTTPException) as error:
        artifact_revision.execute(bind, user_id, turn, artifact_revision.REVISE_TOOL,
            {'artifact_id': previous['id'], **edit})
    assert error.value.status_code == 422
    with client.db_factory() as db:
        assert list(db.scalars(select(Artifact.id))) == [previous['id']]
