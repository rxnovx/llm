"""Exercise downloadable outputs through chat, provider calls, and file retrieval."""
from io import BytesIO
import json
import wave
from zipfile import ZipFile

from docx import Document
from fastapi import HTTPException
import httpx
from pptx import Presentation
import pytest

from app.core.config import get_settings
from app.modules.workflows import archives, artifacts, audio
from test_chat import auth, create, events, send

HTML = (
    '<!doctype html><html lang="ko"><head><style>h1{color:navy}</style>'
    '<script>window.previewRan = true</script>'
    '<meta http-equiv="refresh" content="0; url=https://example.invalid"></head>'
    '<body><h1>발표 자료</h1><a href="https://example.invalid">외부 링크</a>'
    '<a href="#slide2">다음 슬라이드</a><p id="slide2">두 번째 슬라이드</p>'
    f'<!-- {"긴 HTML 파일 " * 900} --></body></html>'
)
SPEECH_URL = 'http://speech.test/v1/audio/speech'
SOUND_URL = 'http://sound.test/v1/audio/generate'
SPEECH_KEY = 'local-speech-test-key'


@pytest.fixture
def wav():
    output = BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        stream.writeframes(b'\x00\x00' * 800)
    return output.getvalue()


def call(name, arguments):
    return {'id': name, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}


def tool_history(payload):
    return {message['tool_name']: json.loads(message['content'])
            for message in payload['messages'] if message['role'] == 'tool'}


@pytest.fixture
def upstream(client, monkeypatch):
    def install(reply, audio_reply=None, *, speech=SPEECH_URL, sound=SOUND_URL, route_error=False):
        monkeypatch.setenv('LLM_CONNECTIONS_JSON', json.dumps([{
            'id': 'local', 'name': 'Local', 'provider': 'vllm', 'base_url': 'http://chat.test/v1',
            'model': 'local-chat', 'supports_tools': True,
        }]))
        monkeypatch.setenv('ASSISTANT_AUTOMATIC_ENABLED', 'true')
        monkeypatch.setenv('AUDIO_SPEECH_URL', speech)
        monkeypatch.setenv('AUDIO_SPEECH_MODEL', 'narrator')
        monkeypatch.setenv('AUDIO_SPEECH_VOICE', 'korean')
        monkeypatch.setenv('AUDIO_SPEECH_API_KEY', SPEECH_KEY)
        monkeypatch.setenv('AUDIO_GENERATION_URL', sound)
        monkeypatch.setenv('AUDIO_GENERATION_MODEL', 'effects')
        get_settings.cache_clear()
        requests = []

        def serve(request):
            requests.append(request)
            if request.url.host != 'chat.test':
                assert audio_reply is not None
                return audio_reply
            assert SPEECH_KEY.encode() not in request.content
            payload = json.loads(request.content)
            if 'response_format' in payload:
                assert json.loads(payload['messages'][-1]['content'])['available']['file_request']
                if route_error:
                    return httpx.Response(400, json={'error': 'structured output unsupported'})
                message = {'content': '{"route":"direct"}'}
            else:
                assert not payload['stream']
                message = reply(payload)
            return httpx.Response(200, json={'choices': [{'message': message}]})

        original = httpx.AsyncClient
        transport = httpx.MockTransport(serve)
        monkeypatch.setattr(httpx, 'AsyncClient', lambda **options: original(transport=transport, **options))
        return requests
    return install


def files_from(response):
    stream = events(response)
    assert stream[-1][0] == 'done', response.text[-3500:]
    files = [file for kind, file in stream if kind == 'artifact']
    assert stream[-1][1]['message']['artifacts'] == files
    return files


def download(client, headers, file):
    return client.get(f"/workflows/artifacts/{file['id']}/download", headers=headers)


def test_html_speech_and_zip_share_a_reply_and_private_downloads(client, account, upstream, wav):
    def reply(payload):
        previous = tool_history(payload)
        names = {tool['function']['name'] for tool in payload['tools']}
        assert names == artifacts.FILE_TOOLS
        if not previous:
            return {'tool_calls': [
                call('create_document', {'name': '발표', 'format': 'html', 'content': HTML}),
                call('create_audio', {'name': '발표 음성', 'kind': 'speech', 'input': '발표를 시작합니다.'}),
            ]}
        assert len(json.dumps(payload, ensure_ascii=False).encode()) < len(HTML.encode())
        if 'create_archive' not in previous:
            return {'tool_calls': [call('create_archive', {
                'name': '발표 묶음', 'file_ids': [previous['create_document']['id'], previous['create_audio']['id']],
            })]}
        return {'content': '발표자료, 발표 음성, ZIP을 만들었습니다.'}

    requests = upstream(reply, httpx.Response(200, content=wav), route_error=True)
    owner, other = auth(account()), auth(account('bob'))
    conversation = create(client, owner)
    response = send(client, owner, conversation, 'HTML 발표자료와 WAV 발표 음성을 만들고 ZIP 파일로 묶어 줘',
                    connection_id='local')
    html, voice, bundle = files_from(response)
    assert [file['format'] for file in [html, voice, bundle]] == ['html', 'wav', 'zip']
    speech, = [request for request in requests if str(request.url) == SPEECH_URL]
    assert json.loads(speech.content) == {'input': '발표를 시작합니다.', 'response_format': 'wav', 'model': 'narrator', 'voice': 'korean'}
    assert speech.headers['authorization'] == f'Bearer {SPEECH_KEY}'
    assert SPEECH_KEY not in response.text
    for file in [html, voice, bundle]:
        assert download(client, other, file).status_code == 404
        assert client.get(f"/workflows/artifacts/{file['id']}", headers=other).status_code == 404
    original = download(client, owner, html)
    assert original.content.decode('utf-8-sig') == HTML
    assert original.headers['content-disposition'].startswith('attachment;')
    preview = client.get(f"/workflows/artifacts/{html['id']}", headers=owner).json()['html_preview']
    assert '발표 자료' in preview and 'href="about:srcdoc#slide2"' in preview
    assert '<script' not in preview and 'example.invalid' not in preview and 'refresh' not in preview
    assert download(client, owner, voice).content == wav
    assert download(client, owner, voice).headers['content-type'] == 'audio/wav'
    with ZipFile(BytesIO(download(client, owner, bundle).content)) as zipped:
        assert zipped.namelist() == ['발표.html', '발표 음성.wav']
        assert zipped.read('발표.html') == original.content and zipped.read('발표 음성.wav') == wav
    saved = client.get('/chat/conversations/' + conversation, headers=owner).json()['messages'][-1]
    assert saved['artifacts'] == [html, voice, bundle]


@pytest.mark.parametrize('speech, expected', [('', 'AUDIO_SPEECH_URL'), (SPEECH_URL, 'PCM WAV')])
def test_failed_audio_keeps_successful_html(client, account, upstream, speech, expected):
    def reply(payload):
        previous = tool_history(payload)
        if not previous:
            return {'tool_calls': [
                call('create_document', {'name': '발표', 'format': 'html', 'content': '<h1>발표</h1>'}),
                call('create_audio', {'name': '낭독', 'kind': 'speech', 'input': '발표합니다.'}),
            ]}
        assert expected in previous['create_audio']['error']
        return {'content': previous['create_audio']['error']}

    requests = upstream(reply, httpx.Response(200, content=b'not-wav'), speech=speech)
    headers = auth(account())
    response = send(client, headers, create(client, headers), 'HTML과 낭독 음성을 만들어 줘', connection_id='local')
    html, = files_from(response)
    assert html['format'] == 'html'
    assert any(kind == 'activity' and event['status'] == 'error' and expected in event['detail']
               for kind, event in events(response))
    assert sum(request.url.host == 'speech.test' for request in requests) == int(bool(speech))


def test_sound_uses_its_own_endpoint_and_duration(client, account, upstream, wav):
    def reply(payload):
        if tool_history(payload):
            return {'content': '파도 소리를 만들었습니다.'}
        return {'tool_calls': [call('create_audio', {
            'name': '파도', 'kind': 'sound', 'input': '파도가 부서지는 소리', 'duration_seconds': 5,
        })]}

    requests = upstream(reply, httpx.Response(200, content=wav))
    headers = auth(account())
    file, = files_from(send(client, headers, create(client, headers), '5초 파도 효과음을 WAV로 만들어 줘', connection_id='local'))
    sound, = [request for request in requests if request.url.host != 'chat.test']
    assert str(sound.url) == SOUND_URL and 'authorization' not in sound.headers
    assert json.loads(sound.content) == {
        'input': '파도가 부서지는 소리', 'response_format': 'wav', 'model': 'effects', 'audio_length': 5,
    }
    assert download(client, headers, file).content == wav


@pytest.mark.parametrize('fmt', ['docx', 'pptx'])
def test_openxml_download_opens_in_its_document_library(client, account, fmt):
    headers = auth(account())
    file, = files_from(send(client, headers, create(client, headers), f'{fmt} 파일을 만들어 줘'))
    assert file['format'] == fmt
    raw = BytesIO(download(client, headers, file).content)
    if fmt == 'docx':
        doc = Document(raw)
        assert '테스트 모드' in doc.paragraphs[1].text and doc.tables[0].cell(1, 0).text == '실행'
    else:
        deck = Presentation(raw)
        assert deck.slides[0].shapes[0].text == '테스트 발표자료'
        preview = client.get(f"/workflows/artifacts/{file['id']}", headers=headers).json()
        assert '1. 테스트 발표자료' in preview['content']


@pytest.mark.parametrize('paths', [
    ['../secret'], ['/absolute'], ['a\\b'], ['C:/file'], ['CON.txt'],
    ['folder/file.txt', 'Folder/FILE.txt'], ['folder', 'folder/file.txt'],
])
def test_zip_paths_are_relative_and_unambiguous(paths):
    with pytest.raises(HTTPException) as error:
        archives.checked_paths(paths)
    assert error.value.status_code == 422


def test_reject_truncated_pcm_data(wav):
    audio.validate_wav(wav)
    with pytest.raises(HTTPException) as error:
        audio.validate_wav(wav[:-2])
    assert error.value.status_code == 502
