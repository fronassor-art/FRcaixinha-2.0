"""Exercise the real Bearer, current_user, ADMIN and Master dependencies over HTTP."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import require_admin, require_master
from app.core.security import create_access_token
from app.db.session import get_db
from app.models import User, UserSession


class _Query:
    def __init__(self, row):
        self.row = row

    def filter(self, *_conditions):
        return self

    def first(self):
        return self.row


class _AuthDb:
    def __init__(self):
        self.user = None
        self.session = None

    def query(self, model):
        assert model is UserSession
        return _Query(self.session)

    def get(self, model, user_id):
        assert model is User
        return self.user if self.user is not None and self.user.id == user_id else None

    def commit(self):
        pass

    def login_as(self, *, user_id: int, role: str, master: bool = False) -> dict:
        now = datetime.now(timezone.utc)
        jti = f"auth-r1-session-{user_id}"
        self.user = SimpleNamespace(id=user_id, role=role, is_active=True, is_master=master)
        self.session = SimpleNamespace(
            user_id=user_id, jti=jti, revoked_at=None,
            last_seen_at=now, expires_at=now + timedelta(minutes=30),
        )
        token = create_access_token(str(user_id), role, jti=jti)
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def protected_http():
    db = _AuthDb()
    api = FastAPI()

    @api.get("/admin")
    def admin(user=Depends(require_admin)):
        return {"user_id": user.id}

    @api.get("/master")
    def master(user=Depends(require_master)):
        return {"user_id": user.id}

    api.dependency_overrides[get_db] = lambda: db
    with TestClient(api) as client:
        yield client, db


@pytest.mark.parametrize("header", [
    None,
    {"Authorization": "Basic dXNlcjpwYXNz"},
    {"Authorization": "Bearer"},
    {"Authorization": "Bearer "},
    {"Authorization": "Bearer malformed-token"},
])
def test_missing_wrong_or_invalid_bearer_is_401(protected_http, header):
    client, _ = protected_http
    response = client.get("/admin", headers=header or {})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_authenticated_member_is_403_at_admin_gate(protected_http):
    client, db = protected_http
    response = client.get("/admin", headers=db.login_as(user_id=1, role="USER"))
    assert response.status_code == 403


def test_authenticated_admin_is_allowed_at_admin_gate(protected_http):
    client, db = protected_http
    response = client.get("/admin", headers=db.login_as(user_id=2, role="ADMIN"))
    assert response.status_code == 200
    assert response.json() == {"user_id": 2}


def test_authenticated_admin_without_master_is_403(protected_http):
    client, db = protected_http
    response = client.get("/master", headers=db.login_as(user_id=2, role="ADMIN"))
    assert response.status_code == 403


def test_active_master_is_allowed(protected_http):
    client, db = protected_http
    response = client.get("/master", headers=db.login_as(user_id=3, role="ADMIN", master=True))
    assert response.status_code == 200
    assert response.json() == {"user_id": 3}
