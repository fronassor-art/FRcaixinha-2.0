"""Contract tests for the persisted Master Administrator authority."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api import deps
from app.api.deps import require_admin
from app.models import User

from test_payment_settlement_v103 import _db


def _require_master(user):
    operation = getattr(deps, "require_master", None)
    assert callable(operation), "require_master deve existir como dependência server-side"
    return operation(user)


def _user(*, role="ADMIN", active=True, master=False):
    return SimpleNamespace(role=role, is_active=active, is_master=master)


def test_admin_common_still_passes_require_admin():
    user = _user(role="ADMIN", active=True, master=False)
    assert require_admin(user) is user


def test_admin_common_fails_require_master():
    with pytest.raises(HTTPException) as exc:
        _require_master(_user(role="ADMIN", active=True, master=False))
    assert exc.value.status_code == 403


def test_regular_user_fails_require_master():
    with pytest.raises(HTTPException) as exc:
        _require_master(_user(role="USER", active=True, master=True))
    assert exc.value.status_code == 403


def test_active_master_admin_passes_require_master():
    user = _user(role="ADMIN", active=True, master=True)
    assert _require_master(user) is user


def test_inactive_master_fails_require_master():
    with pytest.raises(HTTPException) as exc:
        _require_master(_user(role="ADMIN", active=False, master=True))
    assert exc.value.status_code == 403


def test_is_master_with_non_admin_role_fails_require_master():
    with pytest.raises(HTTPException) as exc:
        _require_master(_user(role="USER", active=True, master=True))
    assert exc.value.status_code == 403


def test_database_allows_at_most_one_active_master():
    db = _db()
    first = User(
        name="Master 1",
        email="master-1@test",
        cpf="master-1",
        password_hash="x",
        role="ADMIN",
        is_active=True,
        is_master=True,
    )
    second = User(
        name="Master 2",
        email="master-2@test",
        cpf="master-2",
        password_hash="x",
        role="ADMIN",
        is_active=True,
        is_master=True,
    )
    db.add(first)
    db.commit()
    db.add(second)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.close()


def test_database_rejects_inactive_master():
    db = _db()
    db.add(User(name="Inactive Master", email="inactive-master@test", cpf="inactive-master", password_hash="x", role="ADMIN", is_active=False, is_master=True))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.close()


def test_database_rejects_non_admin_master():
    db = _db()
    db.add(User(name="User Master", email="user-master@test", cpf="user-master", password_hash="x", role="USER", is_active=True, is_master=True))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.close()


def test_database_accepts_active_admin_master():
    db = _db()
    db.add(User(name="Active Master", email="active-master@test", cpf="active-master", password_hash="x", role="ADMIN", is_active=True, is_master=True))
    db.commit()
    assert db.query(User).filter(User.is_master.is_(True)).count() == 1
    db.close()
