import io
import time

import pytest
from PIL import Image
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.security import hash_refresh_token
from app.modules.auth import service
from app.modules.auth.models import OneTimeToken, RefreshSession, User
from conftest import bearer


def test_register_normalization_and_duplicates(client, account):
    token = account("alice")
    assert token["user"]["display_name"] == "Alice"
    duplicate = client.post("/auth/register", json={"username": "ALICE", "email": "another@example.com",
        "password": "StrongPass123!", "accept_terms": True})
    assert duplicate.status_code == 409
    assert client.post("/auth/login", data={"username": "ALICE@EXAMPLE.COM", "password": "StrongPass123!"}).status_code == 200
    with client.db_factory() as db:
        assert db.scalar(select(User)).password_hash.startswith("scrypt$v2$")
        assert db.scalar(select(RefreshSession)).token_hash != token["refresh_token"]


@pytest.mark.parametrize("password", ["short", "longpasswordonly", "123456789012345"])
def test_password_strength(client, password):
    response = client.post("/auth/register", json={"username": "alice", "email": "alice@example.com",
        "password": password, "accept_terms": True})
    assert response.status_code == 422


def test_registration_switch_and_consent(client):
    data = {"username": "alice", "email": "alice@example.com", "password": "StrongPass123!", "accept_terms": False}
    assert client.post("/auth/register", json=data).status_code == 422
    get_settings().public_registration_enabled = False
    data["accept_terms"] = True
    assert client.post("/auth/register", json=data).status_code == 403


def test_login_rate_limit(client, account):
    account()
    for _ in range(5):
        assert client.post("/auth/login", data={"username": "alice", "password": "incorrect"}).status_code == 401
    response = client.post("/auth/login", data={"username": "alice", "password": "incorrect"})
    assert response.status_code == 429
    assert response.headers["retry-after"]


def test_refresh_rotation_and_replay(client, account):
    old = account()
    response = client.post("/auth/refresh", json={"refresh_token": old["refresh_token"]})
    assert response.status_code == 200
    new = response.json()
    assert client.get("/users/me", headers=bearer(new)).status_code == 200
    assert client.get("/users/me", headers=bearer(old)).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": old["refresh_token"]}).status_code == 401
    with client.db_factory() as db:
        assert db.scalar(select(RefreshSession).where(RefreshSession.token_hash == hash_refresh_token(old["refresh_token"]))).revoked_at is not None


def test_failed_rotation_rolls_back_consumption(client, account, monkeypatch):
    old = account()
    original = service.issue_tokens
    def fail(*args, **kwargs):
        raise RuntimeError("simulated database failure")
    monkeypatch.setattr(service, "issue_tokens", fail)
    with pytest.raises(RuntimeError):
        client.post("/auth/refresh", json={"refresh_token": old["refresh_token"]})
    monkeypatch.setattr(service, "issue_tokens", original)
    assert client.post("/auth/refresh", json={"refresh_token": old["refresh_token"]}).status_code == 200


def test_logout_invalidates_access_and_refresh(client, account):
    token = account()
    assert client.post("/auth/logout", json={"refresh_token": token["refresh_token"]}).status_code == 204
    assert client.get("/users/me", headers=bearer(token)).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": token["refresh_token"]}).status_code == 401


def test_logout_all_and_used_token_cannot_revoke_new_sessions(client, account):
    first = account()
    client.post("/auth/logout", json={"refresh_token": first["refresh_token"]})
    second = client.post("/auth/login", data={"username": "alice", "password": "StrongPass123!"}).json()
    client.post("/auth/logout", json={"refresh_token": first["refresh_token"], "all_sessions": True})
    assert client.get("/users/me", headers=bearer(second)).status_code == 200
    third = client.post("/auth/login", data={"username": "alice", "password": "StrongPass123!"}).json()
    client.post("/auth/logout", json={"refresh_token": second["refresh_token"], "all_sessions": True})
    assert client.get("/users/me", headers=bearer(second)).status_code == 401
    assert client.get("/users/me", headers=bearer(third)).status_code == 401


def test_profile_authentication_and_immutable_fields(client, account):
    assert client.get("/users/me").status_code == 401
    token = account()
    headers = bearer(token)
    assert client.patch("/users/me", headers=headers, json={"username": "bob"}).status_code == 409
    changed_email = client.patch("/users/me", headers=headers, json={"email": "new@example.com"})
    assert changed_email.status_code == 409
    assert changed_email.json()["detail"]["code"] == "email_immutable"
    assert client.patch("/users/me", headers=headers, json={"avatar_url": "javascript:alert(1)"}).status_code == 422
    response = client.patch("/users/me", headers=headers, json={"display_name": "  새 이름  "})
    assert response.json()["display_name"] == "새 이름"


def test_locale_validation(client, account):
    headers = bearer(account())
    assert client.patch("/auth/me/locale", headers=headers, json={"locale": "ko", "time_zone": "bad/zone"}).status_code == 422
    assert client.patch("/auth/me/locale", headers=headers, json={"locale": "en", "time_zone": "Asia/Seoul"}).json()["time_zone"] == "Asia/Seoul"


def test_password_change_revokes_all_sessions(client, account):
    first = account()
    second = client.post("/auth/login", data={"username": "alice", "password": "StrongPass123!"}).json()
    assert client.post("/users/me/password", headers=bearer(first), json={"current_password": "wrong", "new_password": "ChangedPass123!"}).status_code == 400
    response = client.post("/users/me/password", headers=bearer(first), json={"current_password": "StrongPass123!", "new_password": "ChangedPass123!"})
    assert response.status_code == 200
    for token in [first, second]:
        assert client.get("/users/me", headers=bearer(token)).status_code == 401
        assert client.post("/auth/refresh", json={"refresh_token": token["refresh_token"]}).status_code == 401
    assert client.post("/auth/login", data={"username": "alice", "password": "ChangedPass123!"}).status_code == 200


def test_session_ownership(client, account):
    alice, bob = account(), account("bob")
    bob_session = client.get("/users/me/sessions", headers=bearer(bob)).json()[0]
    assert client.delete(f"/users/me/sessions/{bob_session['id']}", headers=bearer(alice)).status_code == 404
    assert client.get("/users/me", headers=bearer(bob)).status_code == 200
    own = client.get("/users/me/sessions", headers=bearer(alice)).json()[0]
    assert own["is_current"] is True
    assert client.delete(f"/users/me/sessions/{own['id']}", headers=bearer(alice)).status_code == 204
    assert client.get("/users/me", headers=bearer(alice)).status_code == 401


def test_avatar_validation_and_replacement(client, account):
    token = account()
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(image, "JPEG")
    first = client.post("/users/me/avatar", headers=bearer(token), files={"file": ("../photo.jpg", image.getvalue(), "image/jpeg")})
    assert first.status_code == 201
    url = first.json()["avatar_url"]
    assert url.endswith(".png")
    downloaded = client.get(url)
    assert downloaded.headers["content-type"] == "image/png"
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    rejected = client.post("/users/me/avatar", headers=bearer(token), files={"file": ("image.svg", b'<svg onload="alert(1)"></svg>', "image/png")})
    assert rejected.status_code == 415
    assert client.get(url).status_code == 200
    assert client.patch("/users/me", headers=bearer(token), json={"avatar_url": None}).status_code == 200
    assert client.get(url).status_code == 404
    assert client.get("/users/avatars/invalid/nope.png").status_code == 404


def test_avatar_size_limit(client, account):
    response = client.post("/users/me/avatar", headers=bearer(account()), files={"file": ("large.png", b"x" * (5 * 1024 * 1024 + 1), "image/png")})
    assert response.status_code == 413


def test_email_verification_and_one_time_use(client, monkeypatch):
    settings = get_settings()
    settings.email_provider = "console"
    settings.email_verification_required = True
    captured = []
    monkeypatch.setattr(service.email_delivery, "send_verification_email", lambda email, token: captured.append(token) or True)
    assert client.post("/auth/register", json={"username": "alice", "email": "alice@example.com", "password": "StrongPass123!", "accept_terms": True}).status_code == 201
    assert client.post("/auth/login", data={"username": "alice", "password": "StrongPass123!"}).status_code == 403
    assert client.post("/auth/email-verification/confirm", json={"token": captured[0]}).status_code == 200
    assert client.post("/auth/email-verification/confirm", json={"token": captured[0]}).status_code == 400
    assert client.post("/auth/login", data={"username": "alice", "password": "StrongPass123!"}).status_code == 200


def test_reset_non_disclosure_and_session_revocation(client, account, monkeypatch):
    old = account()
    captured = []
    monkeypatch.setattr(service.email_delivery, "send_password_reset_email", lambda email, token: captured.append(token) or True)
    known = client.post("/auth/password-reset/request", json={"email": "alice@example.com"})
    unknown = client.post("/auth/password-reset/request", json={"email": "missing@example.com"})
    assert known.json() == unknown.json()
    assert known.json()["token"] is None
    data = {"token": captured[0], "new_password": "ResetPassword123!"}
    assert client.post("/auth/password-reset/confirm", json=data).status_code == 200
    assert client.post("/auth/password-reset/confirm", json=data).status_code == 400
    assert client.get("/users/me", headers=bearer(old)).status_code == 401


def test_expired_email_token(client, account, monkeypatch):
    account()
    captured = []
    monkeypatch.setattr(service.email_delivery, "send_password_reset_email", lambda email, token: captured.append(token) or True)
    client.post("/auth/password-reset/request", json={"email": "alice@example.com"})
    with client.db_factory() as db:
        db.scalar(select(OneTimeToken)).expires_at = int(time.time()) - 1
        db.commit()
    assert client.post("/auth/password-reset/confirm", json={"token": captured[0], "new_password": "ResetPassword123!"}).status_code == 400


def test_email_change_and_previous_confirmation_links_cannot_update_account(client, account):
    auth = account()
    headers = bearer(auth)
    new_email = "changed@example.com"
    with client.db_factory() as db:
        token = service.issue_one_time_token(
            db, user=db.get(User, auth["user"]["subject"]), purpose="email_change",
            expire_minutes=10, payload={"new_email": new_email},
        )

    data = {"new_email": new_email, "current_password": "StrongPass123!"}
    assert client.post("/users/me/email-change/request", headers=headers, json=data).status_code == 404
    assert client.post("/users/me/email-change/confirm", json={"token": token}).status_code == 404
    assert client.post("/auth/email-verification/confirm", json={"token": token}).status_code == 400
    profile = client.get("/users/me", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["email"] == "alice@example.com"


def test_deactivate_and_recover(client, account, monkeypatch):
    old = account()
    assert client.post("/users/me/deactivate", headers=bearer(old), json={"current_password": "StrongPass123!"}).status_code == 204
    assert client.get("/users/me", headers=bearer(old)).status_code == 401
    assert client.post("/auth/login", data={"username": "alice", "password": "StrongPass123!"}).status_code == 403
    captured = []
    monkeypatch.setattr(service.email_delivery, "send_account_recovery_email", lambda email, token: captured.append(token) or True)
    client.post("/auth/account-recovery/request", json={"email": "alice@example.com"})
    assert client.post("/auth/account-recovery/confirm", json={"token": captured[0], "new_password": "RecoveredPass123!"}).status_code == 200
    assert client.post("/auth/login", data={"username": "alice", "password": "RecoveredPass123!"}).status_code == 200


def test_google_login_and_last_method_protection(client, monkeypatch):
    settings = get_settings()
    settings.google_oauth_enabled = True
    settings.google_client_id = "test-client"
    monkeypatch.setattr(service, "verify_google_credential", lambda *args, **kwargs: {"sub": "google-subject-123456", "email": "google@example.com", "name": "Google User"})
    data = {"credential": "x" * 120}
    assert client.post("/auth/google", json=data).status_code == 428
    data["accept_terms"] = True
    response = client.post("/auth/google", json=data)
    assert response.status_code == 200
    token = response.json()
    assert token["user"]["has_password"] is False
    assert client.delete("/users/me/oauth/google", headers=bearer(token)).status_code == 409
    assert client.post("/users/me/password/set", headers=bearer(token), json={"new_password": "NewPassword123!"}).status_code == 200
    assert client.post("/auth/refresh", json={"refresh_token": token["refresh_token"]}).status_code == 401


def test_google_does_not_auto_link_matching_email(client, account, monkeypatch):
    account()
    settings = get_settings()
    settings.google_oauth_enabled, settings.google_client_id = True, "test-client"
    monkeypatch.setattr(service, "verify_google_credential", lambda *args, **kwargs: {"sub": "google-subject", "email": "alice@example.com"})
    data = {"credential": "x" * 120, "accept_terms": True}
    assert client.post("/auth/google", json=data).status_code == 409


def test_unrelated_note_routes_are_not_registered(client):
    for path in ["/notes", "/calendar", "/chat", "/blogs", "/auth/register/trial"]:
        assert client.get(path).status_code == 404
    assert client.get('/workspaces').status_code==401


def test_production_configuration():
    valid = dict(_env_file=None, app_env="production", jwt_secret_key="r" * 64,
        auth_proxy_secret="p" * 64, email_provider="disabled", expose_docs=False)
    assert Settings(**valid).app_env == "production"
    for change in [dict(jwt_secret_key="short"), dict(expose_auth_tokens_in_response=True), dict(email_provider="console"), dict(server_reload=True)]:
        with pytest.raises(ValueError):
            Settings(**(valid | change))
