"""Operator-configured embedding, reranking and vector-store adapters."""
import hashlib
import json
import math
from urllib.parse import urlsplit
import httpx
from app.core.config import get_settings
from app.core.tls import assistant_tls

def profile():
    s=get_settings()
    return hashlib.sha256(json.dumps([s.knowledge_embedding_provider,s.knowledge_embedding_url,
        s.knowledge_embedding_model,s.knowledge_vector_store,s.knowledge_qdrant_url],ensure_ascii=False).encode()).hexdigest()

def url(value):
    parsed=urlsplit(value)
    if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('검색 서버 주소 설정을 확인하세요.')
    return value.rstrip('/')

async def request(method,address,body=None,key='',qdrant=False):
    headers=({'api-key':key} if qdrant else {'Authorization':'Bearer '+key}) if key else {}
    try:
        async with httpx.AsyncClient(timeout=45,trust_env=False,follow_redirects=False,headers=headers,verify=assistant_tls()) as client:
            async with client.stream(method,address,json=body) as response:
                if response.status_code==404:return None
                if not response.is_success:raise ValueError('검색 서버가 요청을 거절했습니다. 운영 설정을 확인하세요.')
                raw=bytearray()
                async for part in response.aiter_bytes():
                    raw.extend(part)
                    if len(raw)>4*1024*1024:raise ValueError('검색 서버 응답이 너무 큽니다.')
        return json.loads(raw)
    except (httpx.HTTPError,TypeError,KeyError,json.JSONDecodeError):
        raise ValueError('검색 서버 연결·응답 형식을 확인하세요.') from None

async def embed(texts):
    s=get_settings()
    if s.knowledge_embedding_provider=='disabled':return [[] for _ in texts]
    if not s.knowledge_embedding_model:raise ValueError('임베딩 모델을 설정하세요.')
    address=url(s.knowledge_embedding_url)
    data=await request('POST',address+('/api/embed' if s.knowledge_embedding_provider=='ollama' else '/embeddings'),
        {'model':s.knowledge_embedding_model,'input':texts},s.knowledge_embedding_key.get_secret_value())
    try:
        vectors=data['embeddings'] if s.knowledge_embedding_provider=='ollama' else [r['embedding'] for r in sorted(data['data'],key=lambda r:r['index'])]
        if len(vectors)!=len(texts):raise ValueError()
        dimension=len(vectors[0])
        if not 1<=dimension<=4096:raise ValueError()
        for v in vectors:
            if len(v)!=dimension or any(isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) for n in v):raise ValueError()
            norm=math.sqrt(sum(n*n for n in v))
            if not norm:raise ValueError()
            v[:]=[n/norm for n in v]
        return vectors
    except (ValueError,TypeError,KeyError,IndexError):raise ValueError('임베딩 벡터의 형식·차원을 확인하세요.') from None

async def vector_request(method,path,body=None):
    s=get_settings()
    return await request(method,url(s.knowledge_qdrant_url)+path,body,s.knowledge_qdrant_key.get_secret_value(),True)

def collection(index_profile):return 'llm_knowledge_'+index_profile[:24]

async def upsert(document,chunks,vectors):
    s=get_settings()
    if s.knowledge_vector_store!='qdrant' or not vectors[0]:return
    name=collection(document.index_profile)
    existing=await vector_request('GET','/collections/'+name)
    if existing is None:
        try:await vector_request('PUT','/collections/'+name,{'vectors':{'size':len(vectors[0]),'distance':'Cosine'}})
        except ValueError:
            if await vector_request('GET','/collections/'+name) is None:raise
    await vector_request('PUT','/collections/'+name+'/points?wait=true',{'points':[
        {'id':c['id'],'vector':v,'payload':{'owner_id':document.owner_id,'document_id':document.id,
         'revision':document.revision}} for c,v in zip(chunks,vectors)]})

async def nearest(query_vector,documents,index_profile):
    # Document IDs come from the SQL ACL query, never directly from a model.
    data=await vector_request('POST','/collections/'+collection(index_profile)+'/points/query',
        {'query':query_vector,'filter':{'must':[{'key':'document_id','match':{'any':documents}}]},
         'limit':30,'with_payload':False,'with_vector':False})
    return [] if data is None else [p['id'] for p in data['result']['points']]

async def purge(document_id,owner_id,index_profile,revision=None):
    if not index_profile:return
    conditions=[{'key':'document_id','match':{'value':document_id}},{'key':'owner_id','match':{'value':owner_id}}]
    if revision is not None:conditions.append({'key':'revision','match':{'value':revision}})
    await vector_request('POST','/collections/'+collection(index_profile)+'/points/delete?wait=true',
        {'filter':{'must':conditions}})

async def rerank(query,candidates):
    s=get_settings()
    if not s.knowledge_rerank_url or not candidates:return candidates
    result=await request('POST',url(s.knowledge_rerank_url)+'/rerank',
        {'model':s.knowledge_rerank_model,'query':query,'documents':[r['content'] for r in candidates],'top_n':len(candidates)},
        s.knowledge_rerank_key.get_secret_value())
    try:
        indices=[r['index'] for r in result['results']]
        if len(set(indices))!=len(indices) or any(not isinstance(i,int) or not 0<=i<len(candidates) for i in indices):raise ValueError()
        return [candidates[i] for i in indices]
    except (TypeError,KeyError,ValueError):raise ValueError('재정렬 서버의 응답을 확인하세요.') from None
