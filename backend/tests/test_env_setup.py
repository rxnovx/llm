import os
import json
import base64
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]


@pytest.fixture
def project(tmp_path):
    for name in ("scripts/init_env.py", ".env.example", "backend/.env.example",
                 "frontend/.env.example", "backend/app/core/config.py"):
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPOSITORY / name, destination)
    return tmp_path


def initialize(project, *arguments):
    return subprocess.run([sys.executable, "scripts/init_env.py", *arguments],
                          cwd=project, check=True, capture_output=True, text=True)


def env_values(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if line and not line.startswith("#") and "=" in line)


def test_standalone_setup_generates_private_service_files(project):
    result = initialize(project, "--standalone")
    backend = project / "backend/.env"
    frontend = project / "frontend/.env.local"
    assert not (project / ".env").exists()
    for path in (backend, frontend):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    api, web = env_values(backend), env_values(frontend)
    assert len(api["JWT_SECRET_KEY"]) == 64
    assert api["JWT_SECRET_KEY"] != api["AUTH_PROXY_SECRET"]
    assert api["AUTH_PROXY_SECRET"] == web["AUTH_PROXY_SECRET"]
    keys = json.loads(api['INTEGRATIONS_ENCRYPTION_KEYS'])
    assert len(keys)==1 and len(base64.urlsafe_b64decode(keys[0]))==32
    assert keys[0] not in result.stdout+result.stderr
    assert 'INTEGRATIONS_ENCRYPTION_KEYS' not in web
    assert len(api['TERMINAL_SHARED_SECRET'])==64 and api['TERMINAL_SHARED_SECRET'] not in result.stdout+result.stderr
    assert web["APP_URL"] == "http://localhost:3000"
    assert web["APP_ALLOWED_ORIGINS"] == "http://127.0.0.1:3000"
    for value in (api["JWT_SECRET_KEY"], api["AUTH_PROXY_SECRET"]):
        assert value not in result.stdout + result.stderr


def test_standalone_setup_preserves_existing_root_and_service_settings(project):
    root = project / ".env"
    root.write_text("APP_URL=http://localhost:8080\n")
    initialize(project, "--standalone")
    paths = (root, project / "backend/.env", project / "frontend/.env.local")
    original = {path: path.read_bytes() for path in paths}
    initialize(project, "--standalone")
    assert all(path.read_bytes() == content for path, content in original.items())
    assert root.read_text() == "APP_URL=http://localhost:8080\n"


@pytest.mark.parametrize("compose", [False, True])
def test_shared_setup_keeps_compose_and_existing_secrets(project, compose):
    arguments = ["--compose"] if compose else []
    initialize(project, *arguments, "--postgres")
    values = env_values(project / ".env")
    assert values["APP_URL"] == f"http://localhost:{8080 if compose else 3000}"
    assert values["APP_ALLOWED_ORIGINS"] == ""
    assert len(values["POSTGRES_PASSWORD"]) == 64
    original = (project / ".env").read_bytes()
    initialize(project, "--compose", "--postgres")
    assert (project / ".env").read_bytes() == original
    assert not (project / "backend/.env").exists()


@pytest.mark.parametrize("exported_port", [None, "8124"])
def test_backend_local_env_overrides_shared_env_and_respects_process_env(project, exported_port):
    (project / ".env").write_text("SERVER_PORT=8123\nFRONTEND_URL=http://localhost:8080\n")
    initialize(project, "--standalone")
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"SERVER_PORT", "FRONTEND_URL", "JWT_SECRET_KEY", "AUTH_PROXY_SECRET"}}
    if exported_port:
        environment["SERVER_PORT"] = exported_port
    program = f'''
from app.core.config import Settings
settings = Settings()
assert settings.server_port == {int(exported_port or '8000')}
assert settings.frontend_url == "http://localhost:3000"
assert len(settings.jwt_secret_key) == 64
'''
    subprocess.run([sys.executable, "-c", program], cwd=project / "backend",
                   env=environment, check=True, capture_output=True, text=True)
