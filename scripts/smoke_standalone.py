"""Exercise the real Python + built Next.js servers with an isolated test database.

Run with backend/.venv/bin/python scripts/smoke_standalone.py after npm run build.
"""
import io
import os
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
            client.headers.update({"x-llm-account": "1", "origin": "http://127.0.0.1:3100"})
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
            print("PASS: standalone Python + Next.js signup, login, profile, avatar, refresh, logout and page rendering")
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
