import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
from threading import Event
from uuid import uuid4

import httpx
import pytest
from fastapi import Request
from PIL import Image
from sqlalchemy import select

from app.core.config import get_settings
from app.modules.auth.models import AuditLog, User
from app.modules.workflows import image_queue, images
from app.modules.workflows.models import Artifact, ImageAdmission
from test_admin import headers, operator


@pytest.fixture
def image_servers(client, monkeypatch):
    monkeypatch.setenv('IMAGE_GENERATION_URL', 'http://internal-gen/v1/images/generations')
    monkeypatch.setenv('IMAGE_GENERATION_MODEL', 'illustrator')
    monkeypatch.setenv('IMAGE_GENERATION_API_KEY', 'private-generation-key')
    monkeypatch.setenv('IMAGE_EDIT_URL', 'http://internal-edit/v1/images/edits')
    monkeypatch.setenv('IMAGE_EDIT_MODEL', 'editor')
    monkeypatch.setenv('IMAGE_EDIT_API_KEY', 'private-edit-key')
    get_settings.cache_clear()
    return get_settings()


def test_image_configuration_and_probes_require_current_admin(client, account, image_servers):
    member = headers(account('member'))
    for method, path in [('GET', '/admin/images'), ('POST', '/admin/images/probe')]:
        options = {'json': {'mode': 'generate'}} if method == 'POST' else {}
        assert client.request(method, path, **options).status_code == 401
        assert client.request(method, path, headers=member, **options).status_code == 403
    identity = operator(client, account)
    admin = headers(identity)
    response = client.get('/admin/images', headers=admin)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['generate'] == {'configured': True, 'model': 'illustrator'}
    assert data['edit'] == {'configured': True, 'model': 'editor'}
    assert data['queue'] == {'queued': 0, 'running': 0}
    assert data['limits']['max_concurrent'] == image_servers.image_max_concurrent
    for private in ['internal-gen', 'internal-edit', 'private-generation-key', 'private-edit-key']:
        assert private not in response.text

    malformed = client.post('/admin/images/probe', headers=admin, json={
        'mode': 'generate', 'url': 'http://invalid-target', 'api_key': 'forbidden-secret',
    })
    assert malformed.status_code == 422
    assert 'invalid-target' not in malformed.text and 'forbidden-secret' not in malformed.text
    with client.db_factory() as db:
        db.get(User, identity['user']['subject']).is_global_admin = False
        db.commit()
    assert client.get('/admin/images', headers=admin).status_code == 403
    assert client.post('/admin/images/probe', headers=admin, json={'mode': 'generate'}).status_code == 403


@pytest.mark.parametrize('mode', ['generate', 'edit'])
def test_admin_probe_executes_with_shared_slot_and_keeps_only_audit(client, account, image_servers, monkeypatch, mode):
    admin = headers(operator(client, account))
    with Image.new('RGB', (8, 8)) as sample, io.BytesIO() as output:
        sample.save(output, format='PNG')
        raw = output.getvalue()
    paths = {'generate': '/v1/images/generations', 'edit': '/v1/images/edits'}
    seen = []

    def reply(request):
        with client.db_factory() as db:
            assert image_queue.counts(db) == {'queued': 0, 'running': 1}
        seen.append(request.url.path)
        assert request.method == 'POST'
        if mode == 'edit':
            assert request.headers['content-type'].startswith('multipart/form-data;')
            assert b'name="image"; filename="input.png"' in request.content
        else:
            assert json.loads(request.content)['model'] == 'illustrator'
        return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(raw).decode()}]})

    monkeypatch.setattr(images, '_client', lambda *_args, **_kwargs: httpx.AsyncClient(transport=httpx.MockTransport(reply)))
    response = client.post('/admin/images/probe', headers=admin, json={'mode': mode})
    assert response.status_code == 200, response.text
    assert seen == [paths[mode]]
    result = response.json()
    assert result.pop('elapsed_ms') >= 0
    assert result == {'status': 'ok', 'format': 'png', 'size': len(raw)}
    with client.db_factory() as db:
        assert db.scalar(select(ImageAdmission)) is None
        assert db.scalar(select(Artifact)) is None
        audit = db.scalar(select(AuditLog).where(AuditLog.action == 'admin.image.probe'))
        assert audit.outcome == 'success'
        assert json.loads(audit.details_json) == {'mode': mode, 'code': 'ok'}


@pytest.mark.parametrize('problem,status,code', [
    ('unreachable', 502, 'connection'),
    ('slow', 504, 'timeout'),
    (401, 502, 'authentication'),
    (404, 502, 'configuration'),
    (502, 502, 'upstream'),
    (200, 502, 'response_format'),
])
def test_admin_probe_distinguishes_errors_without_leaking_provider_response(client, account, image_servers, monkeypatch,
                                                                         problem, status, code):
    admin = headers(operator(client, account))

    def reply(request):
        if problem == 'unreachable':
            raise httpx.ConnectError('private upstream address', request=request)
        if problem == 'slow':
            raise httpx.ReadTimeout('private upstream address', request=request)
        return httpx.Response(problem, json={'upstream': 'private upstream body'})

    monkeypatch.setattr(images, '_client', lambda *_: httpx.AsyncClient(transport=httpx.MockTransport(reply)))
    response = client.post('/admin/images/probe', headers=admin, json={'mode': 'generate'})
    assert response.status_code == status, response.text
    assert response.json()['code'] == code
    assert 'private upstream' not in response.text
    with client.db_factory() as db:
        assert db.scalar(select(ImageAdmission)) is None
        audit = db.scalar(select(AuditLog).where(AuditLog.action == 'admin.image.probe'))
        assert audit.outcome == 'failure'
        assert json.loads(audit.details_json) == {'mode': 'generate', 'code': code}


def test_probe_disconnect_cancels_running_provider_and_releases_slot(client, account, image_servers, monkeypatch):
    admin = headers(operator(client, account))
    active = cancelled = False

    async def wait_for_provider(_settings, _mode):
        nonlocal active, cancelled
        active = True
        try:
            await asyncio.sleep(5)
        finally:
            cancelled = True

    async def disconnected(_request):
        return active

    monkeypatch.setattr(images, 'probe', wait_for_provider)
    monkeypatch.setattr(Request, 'is_disconnected', disconnected)
    response = client.post('/admin/images/probe', headers=admin, json={'mode': 'edit'})
    assert response.status_code == 409, response.text
    assert response.json()['code'] == 'cancelled'
    assert cancelled
    with client.db_factory() as db:
        assert db.scalar(select(ImageAdmission)) is None


def test_explicit_probe_cancel_scopes_owner_and_does_not_release_slot_for_a_duplicate(
    client, account, image_servers, monkeypatch,
):
    identity = operator(client, account)
    admin = headers(identity)
    other_identity = account('other-operator')
    with client.db_factory() as db:
        db.get(User, other_identity['user']['subject']).is_global_admin = True
        db.commit()
    probe_id = str(uuid4())
    cancel_path = f'/admin/images/probe/{probe_id}/cancel'
    assert client.post(cancel_path, headers=admin).status_code == 404
    started, stopped = Event(), Event()

    async def wait_for_provider(_settings, _mode):
        started.set()
        try:
            await asyncio.sleep(5)
        finally:
            stopped.set()

    monkeypatch.setattr(images, 'probe', wait_for_provider)
    body = {'mode': 'edit', 'request_id': probe_id}
    with ThreadPoolExecutor() as executor:
        running = executor.submit(client.post, '/admin/images/probe', headers=admin, json=body)
        assert started.wait(3)
        assert client.post(cancel_path, headers=headers(other_identity)).status_code == 404
        duplicate = client.post('/admin/images/probe', headers=admin, json=body)
        assert duplicate.status_code == 409
        assert client.get('/admin/images', headers=admin).json()['queue'] == {'queued': 0, 'running': 1}
        assert client.post(cancel_path, headers=admin).status_code == 204
        cancelled = running.result(timeout=3)
    assert cancelled.status_code == 409 and cancelled.json()['code'] == 'cancelled'
    assert stopped.is_set()
    assert client.get('/admin/images', headers=admin).json()['queue'] == {'queued': 0, 'running': 0}
