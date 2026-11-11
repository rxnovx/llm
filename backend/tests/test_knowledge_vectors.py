import asyncio
import json
import pytest
from sqlalchemy import select
from app.core.config import get_settings
from app.modules.knowledge import providers,service
from app.modules.knowledge.models import VectorGarbage
from test_chat import auth,create
from test_knowledge import base,upload,index,search

def test_qdrant_contract_filters_current_documents_and_cleans_deleted_vectors(client,account,monkeypatch):
    import httpx
    for name,value in {'KNOWLEDGE_EMBEDDING_PROVIDER':'vllm','KNOWLEDGE_EMBEDDING_URL':'http://embed/v1',
        'KNOWLEDGE_EMBEDDING_MODEL':'local','KNOWLEDGE_VECTOR_STORE':'qdrant','KNOWLEDGE_QDRANT_URL':'http://vectors',
        'KNOWLEDGE_QDRANT_KEY':'private-vector-service-key'}.items():monkeypatch.setenv(name,value)
    get_settings.cache_clear();points={};queries=[];deleted=[];created=[]
    def handler(request):
        value=json.loads(request.content) if request.content else None
        if request.url.host=='embed':return httpx.Response(200,json={'data':[{'index':i,'embedding':[3,4]} for i in range(len(value['input']))]})
        assert request.headers['api-key']=='private-vector-service-key'
        path=request.url.path
        if request.method=='GET':return httpx.Response(404 if not created else 200,json={'result':{}})
        if path.endswith('/points/query'):
            queries.append(value);return httpx.Response(200,json={'result':{'points':[{'id':id,'score':1} for id in points]}})
        if path.endswith('/points/delete'):deleted.append(value);return httpx.Response(200,json={'result':{}})
        if path.endswith('/points'):
            for p in value['points']:points[p['id']]=p
        else:created.append(value)
        return httpx.Response(200,json={'result':{}})
    original=httpx.AsyncClient;monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    headers=auth(account());conversation=create(client,headers);kb=base(client,headers);doc=upload(client,headers,base_id=kb);index(client)
    result=search(client,headers,conversation,base_ids=[kb]);assert result.status_code==200,result.text
    assert result.json()['mode']=='hybrid' and result.json()['matches']
    assert queries[0]['filter']['must']==[{'key':'document_id','match':{'any':[doc['id']]}}]
    assert all('content' not in p['payload'] and p['payload']['document_id']==doc['id'] for p in points.values())
    assert client.delete('/knowledge/documents/'+doc['id'],headers=headers).status_code==200
    with client.db_factory() as db:bind=db.get_bind();assert db.scalar(select(VectorGarbage))
    asyncio.run(service.collect_vectors(bind))
    assert deleted[0]['filter']['must'][0]['match']['value']==doc['id']
    with client.db_factory() as db:assert db.scalar(select(VectorGarbage)) is None

def test_sqlite_vector_retrieval_and_profile_change_require_reindex(client,account,monkeypatch):
    monkeypatch.setenv('KNOWLEDGE_EMBEDDING_PROVIDER','vllm');monkeypatch.setenv('KNOWLEDGE_EMBEDDING_URL','http://embed/v1');monkeypatch.setenv('KNOWLEDGE_EMBEDDING_MODEL','first');get_settings.cache_clear()
    async def embeddings(texts):return [[1.,0.] for _ in texts]
    monkeypatch.setattr(providers,'embed',embeddings)
    headers=auth(account());conversation=create(client,headers);kb=base(client,headers);upload(client,headers,base_id=kb);index(client)
    result=client.post('/knowledge/search',headers=headers,json={'conversation_id':conversation,'base_ids':[kb],'query':'unrelated semantic phrase'})
    assert result.json()['mode']=='hybrid' and result.json()['matches']
    monkeypatch.setenv('KNOWLEDGE_EMBEDDING_MODEL','second');get_settings.cache_clear()
    assert search(client,headers,conversation,base_ids=[kb]).status_code==409

def test_reranker_contract_and_invalid_indices(monkeypatch):
    import httpx
    monkeypatch.setenv('KNOWLEDGE_RERANK_URL','http://rank');monkeypatch.setenv('KNOWLEDGE_RERANK_MODEL','local');get_settings.cache_clear();requests=[]
    def handler(request):
        requests.append(json.loads(request.content));return httpx.Response(200,json={'results':[{'index':1,'relevance_score':.9},{'index':0,'relevance_score':.2}]})
    original=httpx.AsyncClient;monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    result=asyncio.run(providers.rerank('query',[{'content':'one'},{'content':'two'}]))
    assert result[0]['content']=='two' and requests[0]['documents']==['one','two']
