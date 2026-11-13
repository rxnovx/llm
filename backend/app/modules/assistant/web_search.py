"""Search a fixed operator-configured SearXNG endpoint with a public query."""

import asyncio
from html import unescape
import json
import re
import unicodedata
from urllib.parse import urlsplit

from fastapi import HTTPException
import httpx

from app.core.tls import assistant_tls

from . import gitlab

TOOL_NAME = 'search_web'
MAX_QUERY = 180
MAX_RESULTS = 5
MAX_RESPONSE = 512 * 1024
SEARCH_REQUEST = re.compile(
    r'(?:(?:웹|인터넷|공개\s*자료|온라인)(?:에서|으로|을)?\s*(?:검색|찾아|조회)|'
    r'(?:최신|실시간|최근\s*발표|오늘(?:의)?)\s*(?:공식|공개|버전|릴리스|뉴스|날씨|환율|주가|정보|시세)|'
    r'(?:공식\s*(?:문서|사이트|출처)|릴리스\s*노트).{0,22}(?:찾|검색|확인|비교)|'
    r'\b(?:search (?:the )?(?:web|internet)|(?:web|online) search|(?:latest|current|official) '
    r'(?:docs|documentation|version|news|weather|exchange rate|release notes|price))\b)', re.I)
PRIVATE_REQUEST = re.compile(
    r'(?:사내|내부|비공개|폐쇄망|첨부(?:한|파일|자료|문서)|우리\s*(?:회사|팀|조직|서비스|서버)|'
    r'내\s*(?:계정|이메일|자료|문서|파일|채팅|대화)|비밀번호|주민등록번호|인증\s*코드|접근\s*토큰|'
    r'\b(?:internal|confidential|private|password|passphrase|secret|bearer|api[_ -]?key|'
    r'access[_ -]?token|attached?|attachment|our (?:company|team|server)|my (?:account|email|chat|files))\b)', re.I)
DENIED_SEARCH = re.compile(
    r'(?:검색|조회)\s*(?:을|은|는)?\s*(?:하지\s*마|하지\s*말|없이|제외)|'
    r'(?:웹|인터넷|온라인|공개\s*검색)(?:은|는|을|를)?\s*(?:사용|이용|검색|조회)\s*(?:하지\s*마|하지\s*말|금지)|'
    r'\b(?:(?:do not|don[\x27’]t|never|without)\s+(?:use (?:the )?)?(?:search|browse|web|internet))\b', re.I)
TOPIC_WORD = re.compile(r'[^\W_]+(?:[.+#-][^\W_]+)*[+#]*', re.UNICODE)
URL_OR_EMAIL = re.compile(
    r'(?:(?:[a-z][a-z0-9+.-]*://|www\.)\S+|[\w.+-]+@[\w.-]+|'
    r'\b(?:\d{1,3}\.){3}\d{1,3}\b|(?<!\w)(?:[\w-]+/)+[\w.-]+)', re.I)
SECRET = re.compile(
    r'(?<![\w-])(?:sk-[\w-]{8,}|(?:gh[pousr]_|github_pat_|glpat-|xox[baprs]-|AIza)[\w-]{8,}|'
    r'[A-Fa-f0-9]{20,}|[A-Za-z0-9_-]{30,})(?![\w-])')
STOP = {
    '웹', '인터넷', '온라인', '검색', '검색해', '검색하고', '검색해서', '검색한', '검색을', '찾아',
    '찾아서', '찾아줘', '찾아주세요', '찾고', '조회', '조회해', '조사해', '비교해', '비교하고', '정리해',
    '알려', '알려줘', '보여', '보여줘', '해줘', '해주세요', '주세요', '해', '줘', '을', '를', '에서',
    '웹에서', '온라인에서', '인터넷에서', '이', '그', '내용', '내용을', '기반으로', '공개', '자료',
    '자료를', 'search', 'find', 'look', 'up', 'web', 'internet', 'online', 'browse',
    'the', 'for', 'and', 'about', 'show', 'tell', 'me', 'please', 'compare', 'summarize',
}


def _url(value, *, endpoint=False):
    if not isinstance(value, str) or len(value) > 2000 or re.search(r'[\x00-\x20\x7f\\]', value):
        return None
    try:
        parsed = urlsplit(value)
        # Reading port validates malformed IPv6 and out-of-range ports.
        port = parsed.port
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or port == 0 or (endpoint and (parsed.query or parsed.fragment))):
            return None
        # HTTPX and the browser should resolve the same authority.
        if not re.fullmatch(r'[A-Za-z0-9.:\-\[\]\u0080-\U0010ffff]+', parsed.netloc):
            return None
        if endpoint and parsed.path in {'', '/'}:
            return value.rstrip('/') + '/search'
    except ValueError:
        return None
    return value


def available(settings):
    return _url(settings.assistant_web_search_url.strip(), endpoint=True) is not None


def _public_question(question):
    return unicodedata.normalize('NFKC', gitlab.without_inline_quotes(gitlab.unquoted(question))).strip()


def requested(question):
    plain = URL_OR_EMAIL.sub(' ', _public_question(question))
    return bool(SEARCH_REQUEST.search(plain) and not DENIED_SEARCH.search(plain) and not PRIVATE_REQUEST.search(plain))


def _matches_suggestion(word, suggestions):
    folded = word.casefold()
    if folded in suggestions:
        return True
    root = re.sub(r'(?:에서|으로|에게|까지|부터|을|를|은|는|이|가|의|에)$', '', folded)
    return root != folded and root in suggestions


def safe_query(query, question):
    """Derive a short query solely from the user's own current message."""
    plain = SECRET.sub(' ', URL_OR_EMAIL.sub(' ', _public_question(question)))
    if not plain or DENIED_SEARCH.search(plain) or PRIVATE_REQUEST.search(plain):
        raise HTTPException(422, '공개 웹에서 검색할 주제를 현재 메시지에 적어 주세요.')
    suggestions = {
        word.casefold() for word in TOPIC_WORD.findall(unicodedata.normalize('NFKC', str(query)[:MAX_QUERY]))
    }
    words = list(dict.fromkeys(word for word in TOPIC_WORD.findall(plain)
                               if 2 <= len(word) <= 32 and word.casefold() not in STOP))
    selected = [word for word in words if _matches_suggestion(word, suggestions)] if query else words
    query_words = []
    for word in selected[:14]:
        if sum(map(len, query_words)) + len(query_words) + len(word) > MAX_QUERY:
            break
        query_words.append(word)
    if not query_words:
        raise HTTPException(422, '공개 웹에서 검색할 주제를 현재 메시지에 적어 주세요.')
    return ' '.join(query_words)


def tool():
    return {'type': 'function', 'function': {
        'name': TOOL_NAME,
        'description': 'Search publicly accessible sources for current information only when relevant. Use short terms present in the current user request. Never pass private chat, attachment, repository, credentials, or other tool results. Cite [WSn] and distinguish the retrieved snippet from the linked page.',
        'parameters': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'minLength': 1, 'maxLength': MAX_QUERY}},
            'required': ['query'], 'additionalProperties': False},
    }}


def client():
    return httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0), trust_env=False,
                             follow_redirects=False, verify=assistant_tls(),
                             headers={'Accept': 'application/json'})


def _plain_text(value, limit):
    if not isinstance(value, str):
        return ''
    decoded = unescape(value[:limit * 4])
    text = re.sub(r'<[^<>]{0,200}>', ' ', decoded)
    text = ''.join(ch for ch in text if ch.isspace() or unicodedata.category(ch) not in {'Cc', 'Cf'})
    return ' '.join(text.split())[:limit]


async def search(settings, query, *, question, limit=MAX_RESULTS):
    address = _url(settings.assistant_web_search_url.strip(), endpoint=True)
    if not address:
        raise HTTPException(503, '운영자가 웹 검색 서버를 연결해야 합니다.')
    public_query = safe_query(query, question)
    try:
        async with asyncio.timeout(10), client() as session:
            async with session.stream('POST', address, data={'q': public_query, 'format': 'json'}) as response:
                if not response.is_success:
                    raise HTTPException(502, '웹 검색 서버가 요청을 거절했습니다. 검색 서버 연결 및 JSON 응답 설정을 확인하세요.')
                raw = bytearray()
                async for part in response.aiter_bytes(chunk_size=65536):
                    if len(raw) + len(part) > MAX_RESPONSE:
                        raise HTTPException(502, '웹 검색 서버 응답이 크기 제한을 초과했습니다.')
                    raw.extend(part)
        payload = json.loads(raw)
    except (httpx.HTTPError, TimeoutError, ValueError, OSError, RecursionError):
        raise HTTPException(502, '웹 검색 서버의 연결이나 응답을 확인하세요.') from None
    rows = payload.get('results') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise HTTPException(502, '웹 검색 서버가 유효한 결과 목록을 보내지 않았습니다.')
    results, seen = [], set()
    for row in rows[:40]:
        if not isinstance(row, dict):
            continue
        url = _url(row.get('url'))
        excerpt = _plain_text(row.get('content'), 520)
        if not url or url in seen or not excerpt:
            continue
        seen.add(url)
        results.append({'app': 'web', 'title': _plain_text(row.get('title'), 160) or urlsplit(url).hostname,
                        'url': url, 'excerpt': excerpt})
        if len(results) >= min(max(limit, 1), MAX_RESULTS):
            break
    return results
