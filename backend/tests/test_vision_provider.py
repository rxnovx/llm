import asyncio
from base64 import b64encode
from io import BytesIO
import json
from random import Random
from uuid import uuid4

import httpx
from PIL import Image
import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.modules.chat import providers, usage
from app.modules.chat.models import ConnectionSetting
from app.modules.workflows import images
from test_admin import operator
from test_chat import auth
from test_model_controls import payload


def raster(format):
    pixels = Random(0).randbytes(64 * 64 * 3)
    output = BytesIO()
    Image.frombytes('RGB', (64, 64), pixels).save(output, format=format)
    return images.validate_raster(output.getvalue())


@pytest.mark.parametrize('kind', ['vllm', 'ollama'])
def test_vision_sends_ordered_current_images_and_accounts_for_tokens(client, account, monkeypatch, kind):
    identity = account()
    user_id = identity['user']['subject']
    with client.db_factory() as db:
        db.add(usage.UserBudget(user_id=user_id, daily_tokens=5000))
        db.commit()
    supplied = [raster('PNG'), raster('JPEG')]
    encoded = [b64encode(image.data).decode('ascii') for image in supplied]
    # Accounting for the wire bytes would reject these images under the budget.
    assert sum(map(len, encoded)) > 5000
    history = [{'role': 'user', 'content': '앞선 질문'}, {'role': 'assistant', 'content': '앞선 답변'},
               {'role': 'user', 'content': '첨부한 두 이미지를 비교해 줘'}]
    original = [dict(item) for item in history]
    connection = providers.Connection(id='local', name='비전', provider=kind, model='vision',
        base_url='http://model.internal/v1' if kind == 'vllm' else 'http://model.internal',
        max_output_tokens=256, supports_vision=True)
    requests = []

    def upstream(request):
        requests.append(request)
        if kind == 'vllm':
            data = [{'choices': [{'delta': {'content': '분석 결과'}}]},
                    {'choices': [], 'usage': {'prompt_tokens': 601, 'completion_tokens': 9}}]
            return httpx.Response(200, text=''.join('data: ' + json.dumps(item) + '\n\n' for item in data)
                                  + 'data: [DONE]\n\n')
        return httpx.Response(200, text=json.dumps({'message': {'content': '분석 결과'}, 'done': True}) + '\n')

    monkeypatch.setattr(providers, 'client', lambda *_: httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    request_id = str(uuid4())

    async def reply():
        with client.db_factory() as db:
            scope = usage.scope.set(usage.Scope(db.get_bind(), user_id, request_id))
        try:
            return ''.join([part async for part in providers.stream_reply(connection, 'vision', history,
                Settings(_env_file=None), images=supplied)])
        finally:
            usage.scope.reset(scope)

    assert asyncio.run(reply()) == '분석 결과'
    request, = requests
    assert request.url.path == ('/v1/chat/completions' if kind == 'vllm' else '/api/chat')
    messages = json.loads(request.content)['messages']
    assert messages[:-1] == original[:-1] and history == original
    if kind == 'vllm':
        assert messages[-1] == {'role': 'user', 'content': [
            {'type': 'text', 'text': original[-1]['content']},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + encoded[0]}},
            {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + encoded[1]}},
        ]}
    else:
        assert messages[-1] == original[-1] | {'images': encoded}
    with client.db_factory() as db:
        call = db.scalar(select(usage.TokenCall).where(usage.TokenCall.request_id == request_id))
        assert call.reservation == 0
        if kind == 'vllm':
            assert call.reported and (call.input_tokens, call.output_tokens) == (601, 9)
        else:
            assert not call.reported
            assert 2 * providers.VISION_IMAGE_TOKENS < call.input_tokens < 5000
            assert call.output_tokens == len('분석 결과'.encode())


@pytest.mark.parametrize(('kind', 'vision'), [('test', True), ('vllm', False)])
def test_vision_cannot_use_demo_or_non_vision_model(monkeypatch, kind, vision):
    connection = providers.Connection(id='local', name='테스트', provider=kind, supports_vision=vision,
        base_url='http://model.internal/v1')
    monkeypatch.setattr(providers, 'client', lambda *_: pytest.fail('Unsupported model cannot call upstream'))

    async def reply():
        return [part async for part in providers.stream_reply(connection, 'vision',
            [{'role': 'user', 'content': '분석해 줘'}], Settings(_env_file=None), images=[raster('PNG')])]

    with pytest.raises(providers.ProviderError, match='이미지'):
        asyncio.run(reply())


def test_vision_capability_round_trips_and_defaults_off_for_old_settings(client, account):
    admin = auth(operator(client, account))
    old = payload(models=[{'name': 'model-a'}])
    with client.db_factory() as db:
        db.add(ConnectionSetting(id=old['id'], configuration=json.dumps(old), revision=1))
        db.commit()
    connections = client.get('/chat/config', headers=admin).json()['connections']
    legacy = next(item for item in connections if item['id'] == 'office')
    assert legacy['supports_vision'] is False and legacy['models'][0]['supports_vision'] is False

    updated = client.patch('/admin/connections/office', headers=admin, json=payload(
        revision=1, supports_vision=True, models=[{'name': 'model-a', 'supports_vision': True}, {'name': 'model-b'}]))
    assert updated.status_code == 200, updated.text
    connections = client.get('/chat/config', headers=admin).json()['connections']
    current = next(item for item in connections if item['id'] == 'office')
    assert current['supports_vision'] is True
    assert [(model['name'], model['supports_vision']) for model in current['models']] == [
        ('model-a', True), ('model-b', False),
    ]
