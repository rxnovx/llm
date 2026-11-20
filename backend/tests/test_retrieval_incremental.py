import asyncio
import pytest
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.modules.knowledge import providers, service
from app.modules.knowledge.models import Document, KnowledgeMember
from test_chat import auth, create
from test_knowledge import base, index, upload


def run_search(client, headers, conversation, kb, query):
    return client.post('/knowledge/search', headers=headers,
        json={'conversation_id': conversation, 'base_ids': [kb], 'query': query})


def test_multilingual_identifier_tokens_preserve_exact_names_and_split_acronyms():
    tokens = set(service.terms('HTTPServer loginUser login_user 사용자의 로그인'))
    assert {'httpserver', 'http', 'server', 'loginuser', 'login_user', 'login', 'user', '사용', '사용자의'} <= tokens
    assert service.lexical_ranks([], 'empty') == []


@pytest.mark.parametrize('indices', [[0, 0], [True, 0], [0, 2]])
def test_embedding_adapter_rejects_duplicate_boolean_or_missing_input_indices(monkeypatch, indices):
    monkeypatch.setenv('KNOWLEDGE_EMBEDDING_PROVIDER', 'vllm')
    monkeypatch.setenv('KNOWLEDGE_EMBEDDING_URL', 'http://embed/v1')
    monkeypatch.setenv('KNOWLEDGE_EMBEDDING_MODEL', 'local')
    get_settings.cache_clear()

    async def response(*args, **kwargs):
        return {'data': [{'index': index, 'embedding': [1., 0.]} for index in indices]}
    monkeypatch.setattr(providers, 'request', response)
    with pytest.raises(ValueError, match='임베딩'):
        asyncio.run(providers.embed(['first', 'second']))


def test_embedding_normalization_is_finite_for_large_finite_wire_values(monkeypatch):
    import math

    monkeypatch.setenv('KNOWLEDGE_EMBEDDING_PROVIDER', 'vllm')
    monkeypatch.setenv('KNOWLEDGE_EMBEDDING_URL', 'http://embed/v1')
    monkeypatch.setenv('KNOWLEDGE_EMBEDDING_MODEL', 'local')
    get_settings.cache_clear()

    async def response(*args, **kwargs):
        return {'data': [{'index': 0, 'embedding': [1e200, 1e200]}]}
    monkeypatch.setattr(providers, 'request', response)
    vector = asyncio.run(providers.embed(['first']))[0]
    assert all(math.isfinite(value) for value in vector)
    assert math.hypot(*vector) == pytest.approx(1.)


def test_keyword_search_matches_filename_and_identifier_components_without_embedding(client, account):
    owner = auth(account())
    conversation = create(client, owner)
    kb = base(client, owner)
    document = upload(client, owner, base_id=kb, filename='session-policy.md', text='loginUser 인증 실패 시 재시도한다.')
    index(client)
    for query in ('session', 'login_user', '인증을'):
        response = run_search(client, owner, conversation, kb, query)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['mode'] == 'lexical' and not result['reranked'] and not result['degraded']
        assert result['matches'][0]['document_id'] == document['id']
        assert result['matches'][0]['sha256'] == document['sha256']
        assert '줄' in result['matches'][0]['location']


def test_embedding_outage_preserves_real_keyword_results_and_does_not_leak_error(client, account, monkeypatch):
    monkeypatch.setenv('KNOWLEDGE_EMBEDDING_PROVIDER', 'vllm')
    monkeypatch.setenv('KNOWLEDGE_EMBEDDING_URL', 'http://private-embed/v1')
    monkeypatch.setenv('KNOWLEDGE_EMBEDDING_MODEL', 'local-embedding')
    get_settings.cache_clear()

    async def embeddings(texts):
        return [[1., 0.] for _ in texts]

    monkeypatch.setattr(providers, 'embed', embeddings)
    owner = auth(account())
    conversation = create(client, owner)
    kb = base(client, owner)
    document = upload(client, owner, base_id=kb, text='회계 마감은 25일입니다.')
    index(client)

    async def unavailable(texts):
        raise ValueError('Bearer private-token-must-not-leak at http://private-embed/v1')

    monkeypatch.setattr(providers, 'embed', unavailable)
    response = run_search(client, owner, conversation, kb, '회계')
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['mode'] == 'lexical' and result['degraded'] and '키워드' in result['notice']
    assert result['matches'][0]['document_id'] == document['id']
    assert 'private-token' not in response.text and 'private-embed' not in response.text
    empty = run_search(client, owner, conversation, kb, 'irrelevant').json()
    assert empty['matches'] == [] and empty['degraded']


@pytest.mark.parametrize('indices', [[True, 0], [0, 0], [], [1], ['1', 0], [2, 0]])
def test_reranker_rejects_non_permutation_or_boolean_indices(monkeypatch, indices):
    monkeypatch.setenv('KNOWLEDGE_RERANK_URL', 'http://rerank')
    get_settings.cache_clear()

    async def response(*args, **kwargs):
        return {'results': [{'index': index} for index in indices]}

    monkeypatch.setattr(providers, 'request', response)
    with pytest.raises(ValueError, match='재정렬'):
        asyncio.run(providers.rerank('query', [{'content': 'first'}, {'content': 'second'}]))


def test_reranker_failure_keeps_keyword_order_and_reports_actual_execution(client, account, monkeypatch):
    monkeypatch.setenv('KNOWLEDGE_RERANK_URL', 'http://rerank')
    get_settings.cache_clear()
    owner = auth(account())
    conversation = create(client, owner)
    kb = base(client, owner)
    document = upload(client, owner, base_id=kb)
    index(client)

    async def fail(*args):
        raise ValueError('private-reranker-password')

    monkeypatch.setattr(providers, 'rerank', fail)
    response = run_search(client, owner, conversation, kb, '연차')
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['matches'][0]['document_id'] == document['id']
    assert not result['reranked'] and result['degraded'] and '기본 검색 순서' in result['notice']
    assert 'private-reranker' not in response.text


def test_reranking_rechecks_membership_after_external_call(client, account, monkeypatch):
    monkeypatch.setenv('KNOWLEDGE_RERANK_URL', 'http://rerank')
    get_settings.cache_clear()
    owner, reader = auth(account()), auth(account('reader'))
    conversation = create(client, reader)
    kb = base(client, owner)
    upload(client, owner, base_id=kb)
    index(client)
    assert client.post(f'/knowledge/bases/{kb}/members', headers=owner, json={'username': 'reader'}).status_code == 200

    async def revoke(query, candidates):
        with client.db_factory() as db:
            db.execute(delete(KnowledgeMember).where(KnowledgeMember.base_id == kb))
            db.commit()
        return candidates

    monkeypatch.setattr(providers, 'rerank', revoke)
    response = run_search(client, reader, conversation, kb, '연차')
    assert response.status_code == 404 and '15일' not in response.text


def test_document_replacement_during_reranking_cannot_return_stale_evidence(client, account, monkeypatch):
    monkeypatch.setenv('KNOWLEDGE_RERANK_URL', 'http://rerank')
    get_settings.cache_clear()
    owner = auth(account())
    conversation = create(client, owner)
    kb = base(client, owner)
    document = upload(client, owner, base_id=kb)
    index(client)

    async def replace(query, candidates):
        with client.db_factory() as db:
            row = db.get(Document, document['id'])
            row.revision += 1
            row.status = 'queued'
            db.commit()
        return candidates

    monkeypatch.setattr(providers, 'rerank', replace)
    response = run_search(client, owner, conversation, kb, '연차')
    assert response.status_code == 409 and '15일' not in response.text


def test_vector_candidates_are_intersected_with_current_sql_scope_before_rerank(client, account, monkeypatch):
    monkeypatch.setenv('KNOWLEDGE_EMBEDDING_PROVIDER', 'vllm')
    monkeypatch.setenv('KNOWLEDGE_EMBEDDING_URL', 'http://embed/v1')
    monkeypatch.setenv('KNOWLEDGE_EMBEDDING_MODEL', 'local')
    monkeypatch.setenv('KNOWLEDGE_VECTOR_STORE', 'qdrant')
    monkeypatch.setenv('KNOWLEDGE_QDRANT_URL', 'http://vectors')
    monkeypatch.setenv('KNOWLEDGE_RERANK_URL', 'http://rerank')
    get_settings.cache_clear()

    async def embeddings(texts):return [[1., 0.] for _ in texts]
    async def no_write(*args):pass
    monkeypatch.setattr(providers, 'embed', embeddings)
    monkeypatch.setattr(providers, 'upsert', no_write)
    owner, other = auth(account()), auth(account('other'))
    conversation = create(client, owner)
    own_kb, other_kb = base(client, owner), base(client, other)
    own_document = upload(client, owner, base_id=own_kb)
    upload(client, other, base_id=other_kb, text='비공개 토큰 SHOULD_NEVER_BE_RERANKED')
    index(client)
    index(client)
    from app.modules.knowledge.models import Chunk
    with client.db_factory() as db:
        foreign_chunk = db.scalar(select(Chunk.id).where(Chunk.document_id != own_document['id']))

    async def nearest(*args):return [foreign_chunk, 'stale-deleted-chunk']
    async def rank(query, candidates):
        assert all(candidate['document_id'] == own_document['id'] for candidate in candidates)
        assert all('SHOULD_NEVER_BE_RERANKED' not in candidate['content'] for candidate in candidates)
        return candidates
    monkeypatch.setattr(providers, 'nearest', nearest)
    monkeypatch.setattr(providers, 'rerank', rank)
    result = run_search(client, owner, conversation, own_kb, '연차').json()
    assert [match['document_id'] for match in result['matches']] == [own_document['id']]


@pytest.mark.parametrize('value', [None, {'result': {}}, {'result': {'points': [None]}},
    {'result': {'points': [{'id': True}]}}, {'result': {'points': [{'id': 'x'}] * 31}}])
def test_vector_adapter_rejects_malformed_ids_without_raw_response_disclosure(monkeypatch, value):
    async def response(*args, **kwargs):return value
    monkeypatch.setattr(providers, 'vector_request', response)
    if value is None:
        assert asyncio.run(providers.nearest([1.], ['doc'], 'profile')) == []
    else:
        with pytest.raises(ValueError, match='벡터 검색'):
            asyncio.run(providers.nearest([1.], ['doc'], 'profile'))
