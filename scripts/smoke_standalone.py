"""Exercise the real Python + built Next.js servers with an isolated test database.

Run with backend/.venv/bin/python scripts/smoke_standalone.py after npm run build.
"""
import io
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

import httpx
from PIL import Image

root = Path(__file__).resolve().parents[1]
web = root / "frontend"
backend = root / "backend"
python = Path(sys.executable)
if not (web / ".next/standalone/server.js").is_file():
    raise SystemExit("Install backend dev dependencies in the active venv and run frontend npm run build first.")

# Assemble the same static assets that the frontend Dockerfile copies.
shutil.copytree(web / ".next/static", web / ".next/standalone/.next/static", dirs_exist_ok=True)
shutil.copytree(web / "public", web / ".next/standalone/public", dirs_exist_ok=True)

processes = []
with tempfile.TemporaryDirectory(prefix="llm-accounts-smoke-") as temporary:
    scratch = Path(temporary)
    logs = []
    try:
        api_log, web_log = [(scratch / name).open("w") for name in ("api.log", "web.log")]
        logs = [api_log, web_log]
        api_env = os.environ | {
            "APP_ENV": "test", "DATABASE_URL": f"sqlite:///{scratch}/app.db",
            "UPLOAD_DIRECTORY": str(scratch / "avatars"), "SERVER_PORT": "8100", "SERVER_HOST": "127.0.0.1",
            "JWT_SECRET_KEY": "standalone-smoke-test-key-at-least-32-bytes",
            "EMAIL_PROVIDER": "disabled", "EMAIL_VERIFICATION_REQUIRED": "false",
            "GOOGLE_OAUTH_ENABLED": "false", "PUBLIC_REGISTRATION_ENABLED": "true",
            "LLM_TEST_MODE": "true", "LLM_CONNECTIONS_JSON": "[]",
        }
        web_env = os.environ | {"APP_URL": "http://127.0.0.1:3100", "PORT": "3100", "HOSTNAME": "127.0.0.1",
            "AUTH_SERVER_URL": "http://127.0.0.1:8100", "AUTH_COOKIE_SECURE": "false"}
        processes.append(subprocess.Popen([str(python), "main.py"], cwd=backend, env=api_env, stdout=api_log, stderr=subprocess.STDOUT))
        processes.append(subprocess.Popen(["node", "server.js"], cwd=web / ".next/standalone", env=web_env, stdout=web_log, stderr=subprocess.STDOUT))
        with httpx.Client(base_url="http://127.0.0.1:3100", timeout=10, trust_env=False) as client:
            for url in ["http://127.0.0.1:8100/ready", "/health"]:
                for _ in range(80):
                    try:
                        if client.get(url).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError(f"Server did not start: {url}")
            assert all(process.poll() is None for process in processes), 'A newly launched server exited; refusing to test another process on the same port'
            client.headers.update({"x-llm-account": "1", "origin": "http://127.0.0.1:3100"})
            # KaTeX fonts must be served by this app, including offline deployments.
            document = client.get('/').text
            styles = set(re.findall(r'href="([^" ]+\.css(?:\?[^" ]*)?)"', document))
            fonts = set()
            for style in styles:
                response = client.get(style)
                assert response.status_code == 200
                fonts.update(re.findall(r'/_next/static/media/[^\s"\')]+\.woff2', response.text))
            assert fonts, 'Bundled math font assets were not found in the rendered styles'
            for font in fonts:
                response = client.get(font)
                assert response.status_code == 200 and len(response.content) > 100
            username = f"standalone_{uuid.uuid4().hex[:12]}"
            response = client.post("/api/auth/register", json={"username": username,
                "email": f"{username}@example.com", "password": "StandalonePass123!", "accept_terms": True})
            assert response.status_code == 201, response.text
            response = client.post("/api/auth/login", json={"loginId": username, "password": "StandalonePass123!"})
            assert response.status_code == 200, response.text
            token = response.json()
            assert "refresh_token" not in token
            assert "HttpOnly" in response.headers["set-cookie"]
            access = {"authorization": f"Bearer {token['access_token']}"}
            assert client.get("/api/account/users/me", headers=access).json()["username"] == username
            assert client.patch("/api/account/users/me", headers=access, json={"display_name": "실행 검증"}).json()["display_name"] == "실행 검증"
            image = io.BytesIO()
            Image.new("RGB", (10, 10), "blue").save(image, "PNG")
            response = client.post("/api/account/users/me/avatar", headers=access,
                files={"file": ("avatar.png", image.getvalue(), "image/png")})
            assert response.status_code == 201, response.text
            path = response.json()["avatar_url"].replace("/users/avatars/", "/api/avatars/")
            assert client.get(path).headers["content-type"] == "image/png"
            config = client.get('/api/chat/config', headers=access)
            assert config.status_code == 200 and config.json()['connections'][0]['is_test']
            response = client.post('/api/chat/conversations', headers=access, json={})
            assert response.status_code == 201, response.text
            conversation = response.json()['id']
            chat_path = f'/api/chat/conversations/{conversation}'
            def turn(content, retry=False, stop=False):
                request_id = str(uuid.uuid4())
                collected, first_delta, terminal = '', None, None
                started = time.monotonic()
                with client.stream('POST', chat_path + '/messages', headers=access, json={
                    'content': content, 'request_id': request_id, 'retry': retry}) as stream:
                    assert stream.status_code == 200
                    assert stream.headers['content-type'].startswith('text/event-stream')
                    kind = ''
                    for line in stream.iter_lines():
                        if line.startswith('event: '): kind = line[7:]
                        elif line.startswith('data: '):
                            data = json.loads(line[6:])
                            if kind == 'delta':
                                first_delta = first_delta or time.monotonic()
                                collected += data['content']
                                if stop:
                                    assert client.post(chat_path + '/cancel', headers=access, json={'request_id':request_id}).status_code == 204
                                    break
                            if kind in {'done', 'error', 'cancelled'}: terminal = kind
                if not stop:
                    assert terminal == 'done' and collected
                    assert first_delta and time.monotonic() - first_delta > .1
                    assert first_delta - started < 5
                return collected
            answer = turn('테스트 대화를 시작할게요')
            detail = client.get(chat_path, headers=access).json()
            assert len(detail['messages']) == 2 and detail['messages'][1]['content'] == answer
            assert detail['messages'][1]['is_test'] and not detail['conversation']['busy']
            partial = turn('응답 중지를 확인할게요', stop=True)
            for _ in range(40):
                detail = client.get(chat_path, headers=access).json()
                if not detail['conversation']['busy']: break
                time.sleep(.1)
            assert partial and not detail['conversation']['busy']
            assert detail['messages'][-1]['status'] == 'cancelled'
            turn('응답 중지를 확인할게요', retry=True)
            assert len(client.get(chat_path, headers=access).json()['messages']) == 4
            assert client.patch(chat_path, headers=access, json={'title':'스트리밍 검증'}).json()['title'] == '스트리밍 검증'
            assert client.get('/api/chat/conversations?offset=1', headers=access).json()['items'] == []
            assert client.delete(chat_path, headers=access).status_code == 204
            assert client.get('/api/admin/overview',headers=access).status_code == 403
            subprocess.run([str(python),'manage_admin.py','grant',username], cwd=backend, env=api_env,
                check=True, capture_output=True, text=True)
            operations = client.get('/api/admin/overview',headers=access)
            assert operations.status_code == 200, operations.text
            assert operations.json()['requests_24h']['total'] == 3
            assert operations.json()['requests_24h']['complete'] == 2 and operations.json()['requests_24h']['cancelled'] == 1
            assert client.get('/api/admin/overview?mode=live',headers=access).json()['requests_24h']['total'] == 0
            assert len(client.get('/api/admin/requests',headers=access).json()['items']) == 3
            members = client.get('/api/admin/users',headers=access).json()['items']
            assert len(members) == 1 and members[0]['is_admin']
            audits = client.get('/api/admin/audit',headers=access).json()['items']
            assert any(row['action']=='admin.role.grant' and row['target']==username for row in audits)
            assert client.post('/api/admin/connections/demo/check',headers=access).json()['status'] == 'test'
            assert client.get('/api/admin/overview').status_code == 401
            assert client.get('/admin').status_code == 200
            response = client.post("/api/auth/refresh")
            assert response.status_code == 200, response.text
            refreshed = response.json()
            assert client.get("/api/account/users/me", headers=access).status_code == 401
            access = {"authorization": f"Bearer {refreshed['access_token']}"}
            assert client.get("/api/account/users/me", headers=access).status_code == 200
            assert client.post("/api/auth/logout").status_code == 204
            assert not any(c.name == "llm_accounts_refresh" for c in client.cookies.jar)
            assert client.get("/api/account/users/me", headers=access).status_code == 401
            assert client.get("/login").status_code == 200
            assert all(process.poll() is None for process in processes), 'A test server exited during validation'
            print("PASS: accounts, streaming chat, persisted titles, stop/retry, operator permissions, metadata and local role setup")
    except Exception:
        for handle in logs:
            handle.flush()
        # Server logs contain request paths, not test credentials or token responses.
        for name in ("api.log", "web.log"):
            print((scratch / name).read_text()[-3000:])
        raise
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        for handle in logs:
            handle.close()
