import asyncio,io,json,time,zipfile
from uuid import uuid4
import pytest
from sqlalchemy import select
from app.core.config import get_settings
from app.modules.chat import providers
from app.modules.chat.models import Conversation,Message,GenerationRun
from app.modules.knowledge import service as knowledge
from app.modules.knowledge.models import Document
from app.modules.workflows.models import Artifact,AttachmentScope,AutoSummary,UploadAttempt
from app.modules.workflows.artifacts import DocumentInput,build
from test_chat import auth,create,send,events
from test_knowledge import upload,index


def last(client,h,c):return client.get('/chat/conversations/'+c,headers=h).json()['messages'][-1]

def project(client,h,name='작업'):
    r=client.post('/workflows/projects',headers=h,json={'name':name,'instructions':'한글로 답하고 이 프로젝트의 문서만 자동 검색'});assert r.status_code==201,r.text;return r.json()


def test_edit_forks_original_history_and_versions_can_be_compared_restored(client,account,monkeypatch):
    h=auth(account());c=create(client,h);counter=iter(['첫 번째 답변','새 버전 답변','후속 답변'])
    async def model(*args):yield next(counter)
    monkeypatch.setattr(providers,'stream_reply',model)
    assert events(send(client,h,c,content='원래 질문'))[-1][0]=='done'
    first=last(client,h,c);v1=first['versions'][0]['id']
    assert events(send(client,h,c,content='원래 질문',retry=True))[-1][0]=='done'
    current=last(client,h,c);assert len(current['versions'])==2
    versions=client.get(f"/workflows/messages/{current['id']}/versions",headers=h).json()
    assert [v['message']['content'] for v in versions['items']]==['첫 번째 답변','새 버전 답변']
    selected=client.post(f"/workflows/messages/{current['id']}/versions/{v1}/select",headers=h)
    assert selected.status_code==200,selected.text
    assert selected.json()['messages'][-1]['content']=='첫 번째 답변'
    send(client,h,c,content='두 번째 질문')
    result=client.post(f'/workflows/conversations/{c}/fork',headers=h,json={'message_id':current['id']})
    assert result.status_code==201,result.text
    child=result.json();assert child['conversation']['parent_id']==c and len(child['messages'])==2
    assert child['messages'][-1]['content']=='첫 번째 답변'
    user=client.get('/chat/conversations/'+c,headers=h).json()['messages'][0]
    result=client.post(f'/workflows/conversations/{c}/fork',headers=h,json={'message_id':user['id'],'content':'수정한 질문'})
    assert result.status_code==201,result.text
    assert result.json()['draft']=='수정한 질문' and result.json()['messages']==[]
    assert client.get('/chat/conversations/'+c,headers=h).json()['messages'][0]['content']=='원래 질문'
    result=client.post(f"/workflows/messages/{current['id']}/versions/{v1}/select",headers=h)
    assert result.status_code==200 and result.json()['forked'] and result.json()['conversation']['id']!=c


def test_server_search_matches_unloaded_bodies_and_treats_wildcards_literally(client,account):
    h=auth(account());other=auth(account('bob'))
    with client.db_factory() as db:
        from app.modules.auth.models import User
        uid=db.scalar(select(User.id).where(User.username=='alice'))
        rows=[Conversation(user_id=uid,connection_id='demo',model='demo',title=f'기록 {i}',updated_at=100+i) for i in range(65)]
        db.add_all(rows);db.flush();target=rows[0].id
        db.add(Message(conversation_id=target,position=0,role='user',content='본문 고유검색 100%_실제',model='demo'));db.commit()
    first=client.get('/chat/conversations',headers=h).json();assert first['has_more'] and target not in [r['id'] for r in first['items']]
    result=client.get('/chat/conversations',params={'q':'고유검색'},headers=h).json()['items'];assert [r['id'] for r in result]==[target] and result[0]['match']['message_id']
    assert len(client.get('/chat/conversations',params={'q':'%_'},headers=h).json()['items'])==1
    assert client.get('/chat/conversations',params={'q':'고유검색'},headers=other).json()['items']==[]


def test_project_context_isolation_revision_move_and_delete_preserve_data(client,account):
    h=auth(account());p=project(client,h);p2=project(client,h,'다른 작업')
    c=client.post('/chat/conversations',headers=h,json={'project_id':p['id']}).json()['id'];c2=create(client,h)
    doc=upload(client,h,base_id=p['base_id']);index(client)
    other=upload(client,h,base_id=p2['base_id'],text='다른 프로젝트만의 정보');index(client)
    with client.db_factory() as db:
        from app.modules.auth.models import User
        uid=db.scalar(select(User.id).where(User.username=='alice'))
        assert [d.id for d in knowledge.automatic_documents(db,uid,c)]==[doc['id']]
        assert knowledge.automatic_documents(db,uid,c2)==[]
        from app.modules.chat.context import plan
        messages,_=plan(db,c,providers.resolve(get_settings(),'demo'),'demo','안녕')
        assert any(p['instructions'] in m['content'] for m in messages)
    assert client.patch('/workflows/projects/'+p['id'],headers=h,json={'name':'변경','instructions':'새 지침','revision':0}).status_code==409
    assert client.get('/chat/conversations',headers=h,params={'project_id':p['id']}).json()['items'][0]['id']==c
    moved=client.patch(f'/workflows/conversations/{c2}/project',headers=h,json={'project_id':p['id']});assert moved.json()['project_id']==p['id']
    assert client.delete('/workflows/projects/'+p['id'],headers=h).status_code==200
    assert client.get('/chat/conversations/'+c,headers=h).json()['conversation']['project_id'] is None
    assert client.get('/knowledge/documents',headers=h,params={'base_id':p['base_id']}).json()['items'][0]['id']==doc['id']


def test_branch_attachment_and_generated_file_survive_parent_delete(client,account):
    h=auth(account());c=create(client,h)
    doc=upload(client,h,conversation=c);index(client)
    send(client,h,c,content='첨부 규정 알려줘',assistant={'document_ids':[doc['id']]})
    message=last(client,h,c)
    file=client.post(f"/workflows/messages/{message['id']}/export",headers=h,json={'format':'md'}).json()
    forked=client.post(f'/workflows/conversations/{c}/fork',headers=h,json={'message_id':message['id']})
    assert forked.status_code==201,forked.text
    child=forked.json();cc=child['conversation']['id'];assert child['messages'][-1]['artifacts'][0]['id']==file['id']
    assert client.delete('/chat/conversations/'+c,headers=h).status_code==204
    assert client.get('/workflows/artifacts/'+file['id']+'/download',headers=h).status_code==200
    assert client.get('/knowledge/documents',headers=h,params={'conversation_id':cc}).json()['items'][0]['id']==doc['id']
    with client.db_factory() as db:
        from app.modules.auth.models import User
        uid=db.scalar(select(User.id).where(User.username=='alice'))
        assert knowledge.document_access(db,uid,doc['id'],conversation_id=cc)
    assert events(send(client,h,cc,content='계속 알려줘',assistant={'document_ids':[doc['id']]}))[-1][0]=='done'


@pytest.mark.parametrize('format',['md','txt','csv','xlsx','pdf'])
def test_real_export_bytes_preview_download_and_acl(client,account,format):
    h=auth(account());other=auth(account('bob'));c=create(client,h);send(client,h,c)
    m=last(client,h,c)
    with client.db_factory() as db:db.get(Message,m['id']).content='한글 보고서\n\n| 항목 | 값 |\n| --- | --- |\n| 영 | 0 |\n| 수식 | =HYPERLINK("https://example.invalid") |';db.commit()
    result=client.post('/workflows/messages/'+m['id']+'/export',headers=h,json={'format':format,'name':'한글 보고서'})
    assert result.status_code==201,result.text
    file=result.json();preview=client.get('/workflows/artifacts/'+file['id'],headers=h);assert preview.status_code==200
    result=client.get('/workflows/artifacts/'+file['id']+'/download',headers=h);assert result.status_code==200
    assert 'filename*=' in result.headers['content-disposition']
    if format=='pdf':
        assert result.content.startswith(b'%PDF-');from pypdf import PdfReader
        assert '한글' in ''.join(p.extract_text() for p in PdfReader(io.BytesIO(result.content)).pages)
    elif format=='xlsx':
        from openpyxl import load_workbook
        book=load_workbook(io.BytesIO(result.content));assert book.active['B3'].value.startswith("'");book.close()
    elif format=='csv':assert b"'=HYPERLINK" in result.content
    else:assert '한글' in result.content.decode()
    assert client.get('/workflows/artifacts/'+file['id']+'/download',headers=other).status_code==404
    assert client.delete('/workflows/artifacts/'+file['id'],headers=other).status_code==404
    assert client.delete('/workflows/artifacts/'+file['id'],headers=h).status_code==200


def test_generated_tool_file_is_real_and_drafts_are_not_downloadable(client,account):
    h=auth(account());c=create(client,h);r=send(client,h,c,content='보고서를 pdf 파일로 만들어줘')
    assert events(r)[-1][0]=='done',r.text[-1800:]
    message=last(client,h,c);assert len(message['artifacts'])==1
    file=message['artifacts'][0];assert client.get('/workflows/artifacts/'+file['id']+'/download',headers=h).content.startswith(b'%PDF-')
    with client.db_factory() as db:
        artifact=db.get(Artifact,file['id']);artifact.status='draft';db.commit()
    assert client.get('/workflows/artifacts/'+file['id']+'/download',headers=h).status_code==404


def test_cancelled_upload_cannot_commit_or_delete_deduplicated_file(client,account):
    h=auth(account());c=create(client,h)
    def attempt():return client.post('/workflows/uploads',headers=h,json={'filename':'자료.md','conversation_id':c}).json()['id']
    def put(id):return client.post('/knowledge/documents',headers=h,data={'conversation_id':c,'upload_id':id},files={'file':('자료.md','자료 내용'.encode())})
    id=attempt();assert client.post('/workflows/uploads/'+id+'/cancel',headers=h).status_code==200
    assert put(id).status_code==409
    id=attempt();first=put(id);assert first.status_code==201,first.text
    original=first.json()['id'];reused=attempt();assert put(reused).json()['id']==original
    client.post('/workflows/uploads/'+reused+'/cancel',headers=h)
    assert client.get('/knowledge/documents',headers=h,params={'conversation_id':c}).json()['items'][0]['id']==original
    client.post('/workflows/uploads/'+id+'/cancel',headers=h)
    assert client.get('/knowledge/documents',headers=h,params={'conversation_id':c}).json()['items']==[]


def test_chat_upload_attempts_keep_same_name_files_and_protect_accepted_documents(client, account):
    headers = auth(account())
    conversation = create(client, headers)

    def attach(raw):
        attempt = client.post('/workflows/uploads', headers=headers,
            json={'filename': '자료.md', 'conversation_id': conversation})
        assert attempt.status_code == 201, attempt.text
        attempt_id = attempt.json()['id']
        uploaded = client.post('/knowledge/documents', headers=headers,
            data={'conversation_id': conversation, 'upload_id': attempt_id},
            files={'file': ('자료.md', raw, 'text/markdown')})
        assert uploaded.status_code == 201, uploaded.text
        return attempt_id, uploaded.json()['id']

    original = '연차 휴가 규정은 연간 15일입니다.'.encode()
    creator, first_id = attach(original)
    index(client)
    unsent, second_id = attach('연차 휴가 규정은 연간 20일입니다.'.encode())
    assert second_id != first_id
    reused, duplicate_id = attach(original)
    assert duplicate_id == first_id

    accepted = send(client, headers, conversation, '연차 휴가 규정을 알려줘',
        assistant={'document_ids': [duplicate_id]})
    assert events(accepted)[-1][0] == 'done', accepted.text
    for attempt_id in [creator, reused]:
        assert client.post(f'/workflows/uploads/{attempt_id}/cancel', headers=headers).status_code == 409

    rejected = send(client, headers, conversation, '두 번째 자료를 읽어줘',
        assistant={'document_ids': [second_id]})
    assert rejected.status_code == 409, rejected.text
    assert client.post(f'/workflows/uploads/{unsent}/cancel', headers=headers).status_code == 200
    with client.db_factory() as db:
        first = db.get(Document, first_id)
        assert first.raw == original and first.revision == 1
        assert db.get(Document, second_id) is None


def test_feedback_is_owner_scoped_operator_reviews_exact_version(client,account):
    h=auth(account());other=auth(account('bob'));c=create(client,h);send(client,h,c);m=last(client,h,c)
    r=client.post('/workflows/messages/'+m['id']+'/feedback',headers=h,json={'category':'source','detail':'출처가 다른 내용'});assert r.status_code==201
    assert client.get('/workflows/feedback',headers=other).json()['items']==[]
    assert client.patch('/workflows/feedback/'+r.json()['id'],headers=h,json={'status':'resolved'}).status_code==403
    from app.modules.auth.models import User
    with client.db_factory() as db:db.scalar(select(User).where(User.username=='bob')).is_global_admin=True;db.commit()
    reports=client.get('/workflows/feedback',headers=other).json()['items'];assert reports[0]['answer']==m['content']
    assert reports[0]['version_id']==m['active_version']
    assert client.patch('/workflows/feedback/'+r.json()['id'],headers=other,json={'status':'resolved'}).status_code==200


def test_automatic_summary_toggle_and_continuation_keep_original(client,account):
    h=auth(account());c=create(client,h)
    with client.db_factory() as db:
        for i in range(18):
            db.add(Message(conversation_id=c,position=i*2,role='user',content=f'질문 {i}',model='demo'))
            db.add(Message(conversation_id=c,position=i*2+1,role='assistant',content=f'대답 {i}',model='demo'))
        db.commit()
    assert events(send(client,h,c,content='계속'))[-1][0]=='done'
    report=client.post(f'/chat/conversations/{c}/context/preview',headers=h,json={}).json();assert report['auto_summary'] and report['summarized_turns']>0
    assert client.patch(f'/workflows/conversations/{c}/auto-summary',headers=h,json={'enabled':False}).json()=={'enabled':False}
    result=client.post(f'/workflows/conversations/{c}/continue',headers=h);assert result.status_code==201,result.text
    assert len(result.json()['messages'])<=12 and result.json()['conversation']['parent_id']==c
    cc=result.json()['conversation']['id']
    report=client.post(f'/chat/conversations/{cc}/context/preview',headers=h,json={}).json();assert report['auto_summary']
    assert len(client.get('/chat/conversations/'+c,headers=h).json()['messages'])==38


def test_failed_stream_preserves_partial_and_explicit_recovery_stage(client,account,monkeypatch):
    h=auth(account());c=create(client,h)
    async def broken(*args):yield '남아 있어야 할 부분 답변';raise providers.ProviderError('서버 연결 중단')
    monkeypatch.setattr(providers,'stream_reply',broken)
    r=send(client,h,c);assert events(r)[-1][0]=='error'
    message=last(client,h,c);assert '부분 답변' in message['content'];assert message['recovery']['phase']=='model'
    assert message['recovery']['can_retry'] and not message['recovery']['can_resume_execution']


def test_export_never_silently_truncates_or_evaluates_code():
    data=DocumentInput(name='표',format='md',columns=['값'],rows=[[0],[False],['=1+1']])
    assert '| 0 |' in build(data).decode() and '| False |' in build(data).decode()
    from app.modules.workflows.artifacts import export_input
    with pytest.raises(ValueError):DocumentInput.model_validate(export_input('큰 표','xlsx','\n'.join('행' for _ in range(501))))


def test_auto_mcp_selection_is_read_only_persists_evidence_and_live_rechecks(client,account,monkeypatch):
    from test_assistant import remote_mcp,registration,admin
    from app.modules.assistant import routing
    h=auth(account());other=auth(account('bob'));admin(client);c=create(client,h)
    monkeypatch.setenv('MCP_SECRETS_JSON','{"internal":"server-only-test-token"}');get_settings.cache_clear()
    with remote_mcp('Documents') as (address,calls,_):
        registered=client.put('/assistant/mcp/servers/docs',headers=h,json=registration('docs','문서 앱',address)|{'description':'회사 일정 조회','keywords':['일정']});assert registered.status_code==200
        schemas=[]
        async def model(connection,model,history,tools,settings):
            schemas.append(tools)
            if not any(m['role']=='tool' for m in history):
                name=next(t['function']['name'] for t in tools if 'lookup' in t['function']['description'])
                return {'role':'assistant','content':'','tool_calls':[{'id':'read','type':'function','function':{'name':name,'arguments':{'query':'오늘 일정'}}}]}
            return {'role':'assistant','content':'조회한 앱 결과입니다. [MS1]','tool_calls':[]}
        monkeypatch.setattr(providers,'tool_reply',model)
        r=send(client,h,c,content='오늘 일정 확인해줘');assert events(r)[-1][0]=='done',r.text[-2000:]
        answer=last(client,h,c);source=answer['sources'][0];assert source['app']=='mcp'
        assert calls==[('read','오늘 일정')] and all('change' not in json.dumps(s) for s in schemas)
        assert answer['routing']['mcp_ids']==['docs'] and answer['routing']['coverage']['app_results']==1
        assert client.get('/workflows/evidence/'+source['evidence_id'],headers=h).status_code==200
        assert client.get('/workflows/evidence/'+source['evidence_id'],headers=other).status_code==404
        fork=client.post(f'/workflows/conversations/{c}/fork',headers=h,json={'message_id':answer['id']}).json();child_source=fork['messages'][-1]['sources'][0]
        assert child_source['evidence_id']!=source['evidence_id']
        assert client.delete('/chat/conversations/'+c,headers=h).status_code==204
        assert client.get('/workflows/evidence/'+child_source['evidence_id'],headers=h).status_code==200
        saved=registered.json();assert client.put('/assistant/mcp/servers/docs',headers=h,json=saved|{'enabled':False}).status_code==200
        assert client.get('/workflows/evidence/'+child_source['evidence_id'],headers=h).status_code==403


def test_unreviewed_output_draft_is_deleted_and_never_emitted(client,account,monkeypatch):
    from app.modules.assistant import routing
    h=auth(account());c=create(client,h);doc=upload(client,h,conversation=c);index(client)
    async def review(*args):return routing.Review(verdict='insufficient',unsupported_claims=['근거 부족'])
    monkeypatch.setattr(routing,'assess',review)
    r=send(client,h,c,content='규정을 pdf 파일로 만들어줘',assistant={'document_ids':[doc['id']]})
    assert events(r)[-1][0]=='done'
    assert all(kind!='artifact' for kind,_ in events(r));assert last(client,h,c)['artifacts']==[]
    with client.db_factory() as db:assert db.scalar(select(Artifact)) is None


def test_workflow_mutations_cannot_access_other_users_or_active_conversations(client,account):
    h=auth(account());other=auth(account('bob'));c=create(client,h);p=project(client,h);send(client,h,c);m=last(client,h,c)
    assert client.patch('/workflows/projects/'+p['id'],headers=other,json={'name':'stolen','revision':1}).status_code==404
    assert client.post(f'/workflows/conversations/{c}/fork',headers=other,json={'message_id':m['id']}).status_code==404
    assert client.get('/workflows/messages/'+m['id']+'/versions',headers=other).status_code==404
    assert client.post('/workflows/messages/'+m['id']+'/feedback',headers=other,json={'category':'other'}).status_code==404
    with client.db_factory() as db:row=db.get(Conversation,c);row.generation_id=str(uuid4());row.generation_until=int(time.time())+60;db.commit()
    assert client.post(f'/workflows/conversations/{c}/fork',headers=h,json={'message_id':m['id']}).status_code==409
    assert client.patch(f'/workflows/conversations/{c}/project',headers=h,json={'project_id':p['id']}).status_code==409


def test_expired_pending_answer_recovers_checkpoint_after_server_restart(client,account):
    h=auth(account());c=create(client,h)
    with client.db_factory() as db:
        row=db.get(Conversation,c);row.generation_id=str(uuid4());row.generation_until=1
        db.add(Message(conversation_id=c,position=0,role='user',model='demo',content='질문'))
        db.add(Message(conversation_id=c,position=1,role='assistant',status='pending',model='demo',content='저장된 부분'));db.commit()
    message=last(client,h,c);assert message['status']=='error' and message['content']=='저장된 부분' and message['recovery']['code']=='interrupted'


def test_write_regeneration_needs_explicit_confirmation_without_network_replay(client,account,monkeypatch):
    from app.modules.assistant.models import TurnContext
    h=auth(account());c=create(client,h);send(client,h,c,content='명시적 작업');m=last(client,h,c)
    with client.db_factory() as db:message=db.get(Message,m['id']);message.assistant_context.data_json=json.dumps({'allow_writes':True});db.commit()
    r=send(client,h,c,content='명시적 작업',retry=True);assert r.status_code==409
    assert last(client,h,c)['content']==m['content']


def test_late_file_renderer_cannot_commit_after_cancellation_or_version_change(client,account):
    from fastapi import HTTPException
    from app.modules.auth.models import User
    from app.modules.workflows import artifacts
    h=auth(account());c=create(client,h);send(client,h,c);m=last(client,h,c)
    with client.db_factory() as db:uid=db.scalar(select(User.id).where(User.username=='alice'));bind=db.get_bind()
    args={'name':'늦은 결과','format':'md','content':'실제 내용'}
    with pytest.raises(HTTPException) as caught:artifacts.create(bind,uid,m['id'],str(uuid4()),args)
    assert caught.value.status_code==409
    with pytest.raises(HTTPException) as caught:artifacts.create(bind,uid,m['id'],str(uuid4()),args,ready=True,expected_content_hash='outdated')
    assert caught.value.status_code==409
    with client.db_factory() as db:assert db.scalar(select(Artifact)) is None


def test_team_project_roles_scope_and_removal(client, account):
    owner, editor, viewer, stranger = [account(name) for name in ['alice', 'bob', 'carol', 'dave']]
    owner_headers, editor_headers, viewer_headers, stranger_headers = map(auth, (owner, editor, viewer, stranger))
    shared = project(client, owner_headers)
    project_path = '/workflows/projects/' + shared['id']
    members = project_path + '/members'
    assert client.post(members, headers=owner_headers, json={'username': 'bob', 'role': 'editor'}).status_code == 200
    assert client.post(members, headers=owner_headers, json={'username': 'carol', 'role': 'viewer'}).status_code == 200
    assert client.post(members, headers=editor_headers, json={'username': 'dave', 'role': 'editor'}).status_code == 404
    assert client.get('/workflows/projects', headers=viewer_headers).json()['items'][0]['role'] == 'viewer'
    assert client.get('/workflows/projects', headers=stranger_headers).json()['items'] == []
    assert client.get(members, headers=stranger_headers).status_code == 404
    form = {'name': '함께 쓰는 프로젝트', 'instructions': '프로젝트 자료를 참고', 'revision': shared['revision']}
    assert client.patch(project_path, headers=viewer_headers, json=form).status_code == 403
    assert client.patch(project_path, headers=editor_headers, json=form).status_code == 200
    assert client.delete(project_path, headers=editor_headers).status_code == 404
    doc = upload(client, editor_headers, base_id=shared['base_id'])
    index(client)
    assert client.get('/knowledge/documents/' + doc['id'] + '/chunks', headers=viewer_headers).status_code == 200
    assert client.delete('/knowledge/documents/' + doc['id'], headers=viewer_headers).status_code == 404
    assert client.delete('/knowledge/bases/' + shared['base_id'], headers=editor_headers).status_code == 404

    selected = {'project_id': shared['id']}
    assert client.post('/chat/conversations', headers=viewer_headers, json=selected).status_code == 403
    created = client.post('/chat/conversations', headers=editor_headers, json=selected)
    assert created.status_code == 201, created.text
    conversation = created.json()['id']
    private = create(client, editor_headers)
    with client.db_factory() as db:
        uid = editor['user']['subject']
        assert [item.id for item in knowledge.automatic_documents(db, uid, conversation)] == [doc['id']]
        assert knowledge.automatic_documents(db, uid, private) == []
    result = send(client, editor_headers, conversation, '공유 연차 규정 알려 줘', assistant={'base_ids': [shared['base_id']]})
    assert events(result)[-1][0] == 'done', result.text[-1200:]
    path = '/chat/conversations/' + conversation
    readable = client.get(path, headers=viewer_headers).json()
    assert readable['conversation']['read_only'] and readable['generation'] is None
    answer = readable['messages'][-1]
    assert answer['sources'][0]['document_id'] == doc['id']
    assert 'tools' not in answer and 'versions' not in answer and 'assistant' not in answer
    assert client.get('/chat/conversations', headers=viewer_headers).json()['items'] == []
    assert client.get('/chat/conversations', headers=viewer_headers, params=selected | {'q': '공유 연차'}).json()['items'][0]['id'] == conversation
    assert client.patch(path, headers=viewer_headers, json={'title': '변경'}).status_code == 404
    assert client.get('/chat/conversations/' + private, headers=owner_headers).status_code == 404
    assert client.get(path, headers=stranger_headers).status_code == 404

    assert client.post(members, headers=owner_headers, json={'username': 'bob', 'role': 'viewer'}).status_code == 200
    assert send(client, editor_headers, conversation).status_code == 403
    assert client.delete(members + '/' + editor['user']['subject'], headers=owner_headers).status_code == 200
    assert client.get(path, headers=editor_headers).json()['conversation']['project_id'] is None
    assert client.get(path, headers=viewer_headers).status_code == 404
    assert client.get('/knowledge/documents/' + doc['id'] + '/chunks', headers=editor_headers).status_code == 404
