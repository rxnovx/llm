import os

os.environ["APP_ENV"] = "test"
os.environ["JWT_SECRET_KEY"] = "test-only-secret-with-at-least-32-bytes"
os.environ["ALLOWED_HOSTS"] = "testserver,localhost,127.0.0.1,api"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import app.main as main_module
from app.core.config import get_settings
from app.db.session import create_db_engine, get_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIRECTORY", str(tmp_path / "avatars"))
    monkeypatch.setenv("EMAIL_PROVIDER", "disabled")
    monkeypatch.setenv("EMAIL_VERIFICATION_REQUIRED", "false")
    monkeypatch.setenv("EXPOSE_AUTH_TOKENS_IN_RESPONSE", "false")
    get_settings.cache_clear()
    settings = get_settings()
    engine = create_db_engine(f"sqlite:///{tmp_path / 'app.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    def db_session():
        with factory() as db:
            try:
                yield db
            except Exception:
                db.rollback()
                raise
    monkeypatch.setattr(main_module, "engine", engine)
    monkeypatch.setattr(main_module, "settings", settings)
    main_module.app.dependency_overrides[get_db] = db_session
    with TestClient(main_module.app) as test_client:
        test_client.db_factory = factory
        yield test_client
    main_module.app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture
def account(client):
    def create(username="alice", email=None):
        response = client.post("/auth/register", json={"username": username,
            "email": email or f"{username}@example.com", "password": "StrongPass123!",
            "accept_terms": True, "display_name": "  Alice  "})
        assert response.status_code == 201, response.text
        response = client.post("/auth/login", data={"username": username, "password": "StrongPass123!"})
        assert response.status_code == 200, response.text
        return response.json()
    return create


def bearer(account):
    return {"Authorization": f"Bearer {account['access_token']}"}
