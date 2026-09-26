"""Registration identity collision and transaction rollback coverage."""

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import router as auth_router
from app.core.cpf import (
    CPFValidationError,
    cpf_storage_candidates,
)
from app.core.seed_safety import seed_cpf_storage_candidates
from app.db.base import Base
from app.db.session import get_db
from app.models import Notification, User, UserSession


CANONICAL_CPF = "52998224725"
FORMATTED_CPF = "529.982.247-25"
OTHER_CPF = "11144477735"


@pytest.fixture()
def registration_client(tmp_path) -> Iterator[tuple[TestClient, Session]]:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'register-unique.sqlite'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    api = FastAPI()
    api.include_router(auth_router)
    api.dependency_overrides[get_db] = lambda: db

    with TestClient(api) as client:
        yield client, db

    api.dependency_overrides.clear()
    db.close()
    engine.dispose()


def _register(client: TestClient, *, email: str, cpf: str):
    return client.post(
        "/auth/register",
        json={
            "name": "Identity Test User",
            "email": email,
            "cpf": cpf,
            "phone": None,
            "password": "StrongTestPassword123!",
            "accept_terms": True,
        },
    )


def _add_user(db: Session, *, email: str, cpf: str) -> User:
    user = User(
        name="Existing User",
        email=email,
        cpf=cpf,
        password_hash="test-hash",
        role="USER",
    )
    db.add(user)
    db.commit()
    return user


@pytest.mark.parametrize(
    ("canonical", "expected"),
    [
        (CANONICAL_CPF, (CANONICAL_CPF, FORMATTED_CPF)),
        (FORMATTED_CPF, (CANONICAL_CPF, FORMATTED_CPF)),
    ],
)
def test_neutral_cpf_candidates_are_exact_and_seed_wrapper_compatible(
    canonical, expected
):
    assert cpf_storage_candidates(canonical) == expected
    assert seed_cpf_storage_candidates(canonical) == expected


def test_cpf_candidates_reject_invalid_input_with_existing_validation_error():
    with pytest.raises(CPFValidationError):
        cpf_storage_candidates("529-982-247-25")


@pytest.mark.parametrize(
    ("stored_cpf", "submitted_cpf"),
    [
        (CANONICAL_CPF, FORMATTED_CPF),
        (FORMATTED_CPF, CANONICAL_CPF),
        (FORMATTED_CPF, FORMATTED_CPF),
    ],
)
def test_register_blocks_equivalent_existing_cpf_without_partial_records(
    registration_client, stored_cpf, submitted_cpf
):
    client, db = registration_client
    _add_user(db, email="existing@example.com", cpf=stored_cpf)

    response = _register(
        client,
        email="new@example.com",
        cpf=submitted_cpf,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "CPF já cadastrado."
    assert submitted_cpf not in response.text
    assert CANONICAL_CPF not in response.text
    assert db.scalar(select(func.count()).select_from(User)) == 1
    assert db.scalar(select(func.count()).select_from(Notification)) == 0
    assert db.scalar(select(func.count()).select_from(UserSession)) == 0


class _PrecheckQuery:
    """Hide only the first email/CPF lookup; keep INSERT constraints real."""

    def __init__(self, query, suppressed):
        self.query = query
        self.suppressed = suppressed
        self.field = None
        self.hide_first = False

    def filter(self, *criteria):
        for criterion in criteria:
            left = getattr(criterion, "left", None)
            field = getattr(left, "key", None)
            if field in self.suppressed:
                self.field = field
                self.hide_first = self.suppressed[field] == 0
                if self.hide_first:
                    self.suppressed[field] += 1
        self.query = self.query.filter(*criteria)
        return self

    def first(self):
        if self.hide_first:
            return None
        return self.query.first()

    def __getattr__(self, name):
        return getattr(self.query, name)


def _hide_initial_identity_prechecks(monkeypatch, db):
    original_query = db.query
    suppressed = {"email": 0, "cpf": 0}

    def query(model):
        return _PrecheckQuery(original_query(model), suppressed)

    monkeypatch.setattr(db, "query", query)
    return suppressed


@pytest.mark.parametrize(
    ("existing_email", "existing_cpf", "submitted_email", "submitted_cpf", "message"),
    [
        (
            "race-email@example.com",
            OTHER_CPF,
            "race-email@example.com",
            CANONICAL_CPF,
            "E-mail já cadastrado.",
        ),
        (
            "owner@example.com",
            CANONICAL_CPF,
            "race-cpf@example.com",
            CANONICAL_CPF,
            "CPF já cadastrado.",
        ),
    ],
)
def test_unique_race_is_classified_after_rollback_and_session_remains_usable(
    registration_client,
    monkeypatch,
    existing_email,
    existing_cpf,
    submitted_email,
    submitted_cpf,
    message,
):
    client, db = registration_client
    _add_user(db, email=existing_email, cpf=existing_cpf)
    suppressed = _hide_initial_identity_prechecks(monkeypatch, db)

    response = _register(client, email=submitted_email, cpf=submitted_cpf)

    assert suppressed == {"email": 1, "cpf": 1}
    assert response.status_code == 409
    assert response.json()["detail"] == message
    assert submitted_cpf not in response.text
    # A normal query on the same Session proves rollback completed first.
    assert db.scalar(select(func.count()).select_from(User)) == 1
    assert db.scalar(select(func.count()).select_from(Notification)) == 0
    assert db.scalar(select(func.count()).select_from(UserSession)) == 0


def test_email_precedes_cpf_when_both_identities_conflict(registration_client):
    client, db = registration_client
    _add_user(db, email="both@example.com", cpf=CANONICAL_CPF)

    response = _register(client, email="both@example.com", cpf=CANONICAL_CPF)

    assert response.status_code == 409
    assert response.json()["detail"] == "E-mail já cadastrado."


def test_unrelated_integrity_error_is_reraised_after_rollback(
    registration_client, monkeypatch
):
    client, db = registration_client
    original_error = IntegrityError("unrelated constraint", {}, RuntimeError("opaque"))

    def fail_commit():
        raise original_error

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(IntegrityError) as caught:
        _register(client, email="unrelated@example.com", cpf=CANONICAL_CPF)

    assert caught.value is original_error
    assert db.scalar(select(func.count()).select_from(User)) == 0
    assert db.scalar(select(func.count()).select_from(Notification)) == 0
    assert db.scalar(select(func.count()).select_from(UserSession)) == 0
