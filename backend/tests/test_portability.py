import os
from pathlib import Path
import subprocess
import sys


def test_sqlite_account_flow_without_postgres_imports(tmp_path):
    # Model an environment where no PostgreSQL driver is installed, even if the
    # developer's current venv happens to include an optional database driver.
    program = '''
import importlib.abc
import sys
class NoPostgres(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"psycopg", "psycopg_binary", "psycopg_c"}:
            raise ModuleNotFoundError("PostgreSQL is not installed")
sys.meta_path.insert(0, NoPostgres())
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as client:
    assert client.get("/ready").status_code == 200
    response = client.post("/auth/register", json={"username":"portable",
        "email":"portable@example.com", "password":"PortablePass123!", "accept_terms":True})
    assert response.status_code == 201, response.text
    response = client.post("/auth/login", data={"username":"portable", "password":"PortablePass123!"})
    assert response.status_code == 200
    token = response.json()
    assert client.get("/users/me", headers={"Authorization":"Bearer " + token["access_token"]}).status_code == 200
'''
    environment = os.environ | {
        "APP_ENV": "test", "DATABASE_URL": f"sqlite:///{tmp_path / 'portable.db'}",
        "UPLOAD_DIRECTORY": str(tmp_path / "avatars"), "EMAIL_PROVIDER": "disabled",
        "EMAIL_VERIFICATION_REQUIRED": "false", "PUBLIC_REGISTRATION_ENABLED": "true",
        "ALLOWED_HOSTS": "testserver,localhost,127.0.0.1",
    }
    subprocess.run([sys.executable, "-c", program], env=environment,
                   cwd=Path(__file__).resolve().parents[1], check=True)


def test_timezone_without_system_database():
    program = '''
from zoneinfo import TZPATH
from app.core.locale import validate_time_zone
assert not TZPATH
assert validate_time_zone("Asia/Seoul") == "Asia/Seoul"
'''
    subprocess.run([sys.executable, "-c", program], env=os.environ | {"PYTHONTZPATH": ""},
                   cwd=Path(__file__).resolve().parents[1], check=True)
