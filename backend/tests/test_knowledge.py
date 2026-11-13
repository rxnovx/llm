import asyncio
import io
import json
import time
import pytest
from sqlalchemy import select,update
from app.core.config import get_settings
from app.modules.knowledge import service,providers
from app.modules.knowledge.models import Chunk,Document
from app.modules.knowledge.extract import extract
from app.modules.chat import providers as chat_providers
from test_chat import auth,create,events,send

def base(client,headers,name='업무 자료'):
    r=client.post('/knowledge/bases',headers=headers,json={'name':name});assert r.status_code==201,r.text
    return r.json()['id']

def upload(client,headers,*,base_id=None,conversation=None,text='휴가 규정: 연차는 연간 15일입니다. 반차도 사용할 수 있습니다.',filename='규정.md',raw=None):
    r=client.post('/knowledge/documents',headers=headers,data={'base_id':base_id} if base_id else {'conversation_id':conversation},
        files={'file':(filename,text.encode() if raw is None else raw,'application/octet-stream')})
    assert r.status_code==201,r.text
    return r.json()

def index(client):
    with client.db_factory() as db:bind=db.get_bind()
    assert asyncio.run(service.index_one(bind))

def search(client,headers,conversation,**selection):
    return client.post('/knowledge/search',headers=headers,json={'conversation_id':conversation,'query':'연차 휴가 규정',**selection})

def test_private_shared_acl_and_revocation_apply_before_retrieval(client,account):
    alice,bob=auth(account()),auth(account('bob'));ca,cb=create(client,alice),create(client,bob)
    kb=base(client,alice);doc=upload(client,alice,base_id=kb);index(client)
    assert search(client,bob,cb,base_ids=[kb]).status_code==404
    assert client.get('/knowledge/documents/'+doc['id']+'/chunks',headers=bob).status_code==404
    assert client.post(f'/knowledge/bases/{kb}/members',headers=alice,json={'username':'bob'}).status_code==200
    result=search(client,bob,cb,base_ids=[kb]);assert result.status_code==200,result.text
    match=result.json()['matches'][0];assert '15일' in match['content'] and match['sha256']==doc['sha256']
    assert client.delete('/knowledge/documents/'+doc['id'],headers=bob).status_code==404
    user=client.get(f'/knowledge/bases/{kb}/members',headers=alice).json()['items'][0]['id']
    client.delete(f'/knowledge/bases/{kb}/members/{user}',headers=alice)
    assert search(client,bob,cb,base_ids=[kb]).status_code==404
    assert search(client,alice,ca,base_ids=[kb]).json()['mode']=='lexical'

def test_attachments_are_owner_and_conversation_scoped_and_expire(client,account):
    alice=auth(account());c1,c2=create(client,alice),create(client,alice)
    doc=upload(client,alice,conversation=c1);index(client)
    assert search(client,alice,c2,document_ids=[doc['id']]).status_code==404
    assert search(client,alice,c1,document_ids=[doc['id']]).json()['matches']
    with client.db_factory() as db:db.execute(update(Document).where(Document.id==doc['id']).values(expires_at=int(time.time())-1));db.commit()
    assert search(client,alice,c1,document_ids=[doc['id']]).status_code==404
    with client.db_factory() as db:bind=db.get_bind()
    asyncio.run(service.index_one(bind))
    with client.db_factory() as db:assert db.get(Document,doc['id']) is None

def test_upload_limits_format_and_foreign_scope(client,account,monkeypatch):
    alice,bob=auth(account()),auth(account('bob'));conversation=create(client,bob)
    assert client.post('/knowledge/documents',headers=alice,data={'conversation_id':conversation},files={'file':('a.md',b'private')}).status_code==404
    conversation=create(client,alice)
    assert client.post('/knowledge/documents',headers=alice,data={'conversation_id':conversation},files={'file':('run.exe',b'bad')}).status_code==422
    monkeypatch.setenv('KNOWLEDGE_MAX_UPLOAD_BYTES','1024');get_settings.cache_clear()
    assert client.post('/knowledge/documents',headers=alice,data={'conversation_id':conversation},files={'file':('a.txt',b'x'*1025)}).status_code==413
    assert client.post('/knowledge/documents',headers=alice,content=b'x'*(300*1024)).status_code==413

def test_queued_failure_reindex_and_current_source_validation(client,account):
    headers=auth(account());conversation=create(client,headers);kb=base(client,headers)
    doc=upload(client,headers,base_id=kb)
    assert search(client,headers,conversation,base_ids=[kb]).status_code==409
    index(client)
    match=search(client,headers,conversation,base_ids=[kb]).json()['matches'][0]
    source=f"/knowledge/documents/{doc['id']}/chunks/{match['chunk_id']}"
    assert client.get(source,headers=headers).json()['revision']==1
    client.post('/knowledge/documents/'+doc['id']+'/reindex',headers=headers);index(client)
    assert client.get(source,headers=headers).status_code==404
    assert search(client,headers,conversation,base_ids=[kb]).json()['matches'][0]['revision']==2
    client.delete('/knowledge/documents/'+doc['id'],headers=headers)
    assert search(client,headers,conversation,base_ids=[kb]).json()['matches']==[]

def test_general_chat_rag_sources_persist_without_gitlab(client,account):
    headers=auth(account());conversation=create(client,headers);kb=base(client,headers)
    upload(client,headers,base_id=kb);index(client)
    r=send(client,headers,conversation,content='연차 휴가 규정 알려줘',assistant={'base_ids':[kb]})
    assert events(r)[-1][0]=='done',r.text[-1500:]
    saved=client.get(f'/chat/conversations/{conversation}',headers=headers).json()['messages'][-1]
    assert saved['assistant']['base_ids']==[kb] and saved['sources'][0]['app']=='knowledge'
    assert saved['sources'][0]['filename']=='규정.md' and '[KS1]' in saved['content']
    assert any(t['name']=='knowledge_search' for t in saved['tools'])

def test_rag_also_works_with_non_tool_model_and_enforces_input_budget(client,account,monkeypatch):
    monkeypatch.setenv('LLM_CONNECTIONS_JSON',json.dumps([{'id':'plain','name':'Plain','provider':'vllm','base_url':'http://local/v1','model':'plain'}]));get_settings.cache_clear()
    headers=auth(account());conversation=create(client,headers);kb=base(client,headers);upload(client,headers,base_id=kb);index(client)
    payloads=[]
    async def stream(connection,model,history,settings):
        payloads.append(history);yield '연차는 15일입니다. [KS1]'
    monkeypatch.setattr(chat_providers,'stream_reply',stream)
    response=send(client,headers,conversation,content='연차 규정?',connection_id='plain',model='plain',assistant={'base_ids':[kb],'automatic':False})
    assert events(response)[-1][0]=='done',response.text[-1500:]
    assert '15일' in payloads[0][-1]['content'] and 'Retrieved evidence' in payloads[0][-1]['content']


@pytest.mark.parametrize('query', ['첨부한 자료의 내용을 알려줘.', '정산 마감일을 알려줘'])
def test_document_attachments_reach_text_model_without_manual_search(client, account, monkeypatch, query):
    monkeypatch.setenv('LLM_CONNECTIONS_JSON', json.dumps([{
        'id': 'plain', 'name': 'Plain', 'provider': 'vllm', 'base_url': 'http://local/v1', 'model': 'plain',
    }]))
    get_settings.cache_clear()
    owner = auth(account())
    conversation = create(client, owner)
    another = create(client, owner)
    first = upload(client, owner, conversation=conversation, filename='회계.md', text='정산 마감일은 3월 27일.')
    index(client)
    second = upload(client, owner, conversation=conversation, filename='목록.md', text='사과 · 포도 · 블루베리')
    index(client)
    private = upload(client, owner, conversation=another, filename='비공개.md', text='비공개 첨부의 내용')
    index(client)
    messages = []

    async def stream(connection, model, history, settings):
        messages.extend(history)
        yield '첫 파일은 정산 일정 [KS1], 두 번째 파일은 과일 목록 [KS2]입니다.'

    monkeypatch.setattr(chat_providers, 'stream_reply', stream)
    response = send(client, owner, conversation, query, connection_id='plain',
        assistant={'document_ids': [first['id'], second['id']], 'automatic': False})
    status, result = events(response)[-1]
    assert status == 'done', response.text[-2000:]
    assert [source['document_id'] for source in result['message']['sources']] == [first['id'], second['id']]
    content = messages[-1]['content']
    assert '3월 27일' in content and '블루베리' in content and '비공개 첨부' not in content
    denied = send(client, owner, conversation, query, connection_id='plain',
        assistant={'document_ids': [private['id']], 'automatic': False})
    assert denied.status_code == 404


def test_review_receives_more_than_six_cited_attachments(client, account, monkeypatch):
    from app.modules.assistant import routing

    monkeypatch.setenv('LLM_CONNECTIONS_JSON', json.dumps([{
        'id': 'plain', 'name': 'Plain', 'provider': 'vllm', 'base_url': 'http://local/v1', 'model': 'plain',
        'context_window': 16384, 'max_output_tokens': 1024,
    }]))
    get_settings.cache_clear()
    owner = auth(account())
    conversation = create(client, owner)
    document_ids = []
    for index_number in range(7):
        doc = upload(client, owner, conversation=conversation, filename=f'자료-{index_number}.txt', text=f'고유 본문 {index_number}')
        document_ids.append(doc['id'])
        index(client)
    expected = [f'KS{number}' for number in range(1, 8)]

    async def stream(connection, model, history, settings):
        assert all(f'[{name}]' in history[-1]['content'] for name in expected)
        yield ' '.join(f'[{name}]' for name in expected)

    async def assess(connection, model, question, draft, excerpts, settings):
        assert [item['id'] for item in excerpts] == expected
        return routing.Review(verdict='supported')

    monkeypatch.setattr(chat_providers, 'stream_reply', stream)
    monkeypatch.setattr(routing, 'assess', assess)
    response = send(client, owner, conversation, '첨부한 자료의 내용을 알려줘.', connection_id='plain',
        assistant={'document_ids': document_ids})
    status, result = events(response)[-1]
    assert status == 'done', response.text[-2000:]
    assert [source['id'] for source in result['message']['sources']] == expected
    assert result['message']['routing']['grounding']['verdict'] == 'supported'

@pytest.mark.parametrize('suffix',['.txt','.md','.csv','.json','.html','.docx','.xlsx','.pdf'])
def test_extractors_preserve_document_text_and_source_location(suffix):
    text='Vacation allowance is 15 days.'
    if suffix=='.docx':
        from docx import Document as Word
        document=Word();document.add_paragraph(text);data=io.BytesIO();document.save(data);raw=data.getvalue()
    elif suffix=='.xlsx':
        from openpyxl import Workbook
        book=Workbook();book.active.append([text]);data=io.BytesIO();book.save(data);book.close();raw=data.getvalue()
    elif suffix=='.pdf':
        from pypdf import PdfWriter
        from pypdf.generic import NameObject,DictionaryObject,DecodedStreamObject
        writer=PdfWriter();page=writer.add_blank_page(600,800)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(f'BT /F1 12 Tf 20 700 Td ({text}) Tj ET'.encode());page[NameObject('/Contents')]=writer._add_object(stream)
        data=io.BytesIO();writer.write(data);raw=data.getvalue()
    else:raw=(f'<p>{text}</p><script>private()</script>' if suffix=='.html' else text).encode()
    chunks=extract(raw,'file'+suffix)
    assert any('15 days' in c['content'] for c in chunks) and chunks[0]['location']
    assert all('private()' not in c['content'] for c in chunks)

def test_extraction_subprocess_and_error_status(client,account):
    headers=auth(account());kb=base(client,headers)
    good=upload(client,headers,base_id=kb,text='문서 텍스트');index(client)
    bad=upload(client,headers,base_id=kb,filename='bad.pdf',raw=b'not a pdf');index(client)
    with client.db_factory() as db:
        assert db.get(Document,good['id']).status=='ready'
        row=db.get(Document,bad['id']);assert row.status=='error' and row.error

def test_unchanged_upload_reuses_index_and_changed_file_advances_revision(client,account):
    headers=auth(account());kb=base(client,headers)
    doc=upload(client,headers,base_id=kb);index(client)
    duplicate=upload(client,headers,base_id=kb)
    assert duplicate['id']==doc['id'] and duplicate['status']=='ready' and duplicate['revision']==1
    changed=upload(client,headers,base_id=kb,text='휴가 규정이 20일로 바뀌었습니다.')
    assert changed['id']==doc['id'] and changed['revision']==2 and changed['status']=='queued'
    index(client)

def test_an_expired_worker_cannot_publish_over_a_new_lease(client,account,monkeypatch):
    headers=auth(account());kb=base(client,headers);doc=upload(client,headers,base_id=kb)
    async def newer_lease(raw,filename):
        with client.db_factory() as db:
            row=db.get(Document,doc['id']);row.lease_until+=1000;db.commit()
        return [{'location':'본문','content':'소스 텍스트'}]
    monkeypatch.setattr(service,'parse',newer_lease);index(client)
    with client.db_factory() as db:
        row=db.get(Document,doc['id']);assert row.status=='indexing' and row.chunk_count==0

@pytest.mark.parametrize('provider',['vllm','ollama'])
def test_embedding_contract_and_finite_normalized_vectors(monkeypatch,provider):
    import httpx
    monkeypatch.setenv('KNOWLEDGE_EMBEDDING_PROVIDER',provider);monkeypatch.setenv('KNOWLEDGE_EMBEDDING_URL','http://embedding'+('/v1' if provider=='vllm' else ''))
    monkeypatch.setenv('KNOWLEDGE_EMBEDDING_MODEL','local');get_settings.cache_clear();captured=[]
    def handler(request):
        captured.append(request)
        return httpx.Response(200,json={'embeddings':[[3,4]]} if provider=='ollama' else {'data':[{'index':0,'embedding':[3,4]}]})
    original=httpx.AsyncClient;monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    assert asyncio.run(providers.embed(['문서']))==[[.6,.8]]
    assert captured[0].url.path==('/api/embed' if provider=='ollama' else '/v1/embeddings')

def test_deleted_or_revoked_knowledge_is_not_returned_after_remote_search(client,account,monkeypatch):
    headers=auth(account());conversation=create(client,headers);kb=base(client,headers);doc=upload(client,headers,base_id=kb);index(client)
    async def revoked(query,candidates):
        await asyncio.to_thread(client.delete,'/knowledge/documents/'+doc['id'],headers=headers);return candidates
    monkeypatch.setattr(providers,'rerank',revoked)
    assert search(client,headers,conversation,base_ids=[kb]).json()['matches']==[]
