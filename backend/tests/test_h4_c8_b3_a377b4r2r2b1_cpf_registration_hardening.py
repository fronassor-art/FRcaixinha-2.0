"""Canonical CPF validation and /auth/register hardening tests."""

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.api.auth import router as auth_router
from app.core.cpf import CPFValidationError, normalize_cpf
from app.db.base import Base
from app.db.session import get_db
from app.models import Notification, User, UserSession


VALID_CPF = "52998224725"
FORMATTED_CPF = "529.982.247-25"
OTHER_VALID_CPF = "11144477735"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (VALID_CPF, VALID_CPF),
        (FORMATTED_CPF, VALID_CPF),
        (f"  {FORMATTED_CPF}  ", VALID_CPF),
    ],
)
def test_normalize_cpf_returns_only_canonical_ascii_digits(value, expected):
    normalized = normalize_cpf(value)

    assert normalized == expected
    assert len(normalized) == 11
    assert normalized.isascii()
    assert all("0" <= character <= "9" for character in normalized)


@pytest.mark.parametrize(
    "value",
    [
        "52998224724",  # first check digit valid, second invalid
        "52998224825",  # first check digit invalid
        "00000000000",
        "111.111.111-11",
        "529x98224725",
        "529-982-247-25",
        "529 982 24725",
        "",
        "   ",
        None,
        "５２９９８２２４７２５",
    ],
)
def test_normalize_cpf_rejects_invalid_values_without_echoing_input(value):
    with pytest.raises(CPFValidationError) as caught:
        normalize_cpf(value)

    assert str(caught.value) == "CPF inválido."
    assert value is None or str(value) not in str(caught.value)


@pytest.fixture()
def registration_client(tmp_path) -> Iterator[tuple[TestClient, Session]]:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'cpf-register.sqlite'}")

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


def _register(client: TestClient, cpf: str):
    return client.post(
        "/auth/register",
        json={
            "name": "CPF Test User",
            "email": "cpf-register@example.test",
            "cpf": cpf,
            "phone": None,
            "password": "StrongTestPassword123!",
            "accept_terms": True,
        },
    )


@pytest.mark.parametrize("cpf", [VALID_CPF, FORMATTED_CPF])
def test_register_accepts_valid_cpf_and_persists_canonical_digits(
    registration_client, cpf
):
    client, db = registration_client

    response = _register(client, cpf)

    assert response.status_code == 200
    user = db.scalar(select(User).where(User.email == "cpf-register@example.test"))
    assert user is not None
    assert user.cpf == VALID_CPF
    assert user.cpf.isascii()
    assert len(user.cpf) == 11


@pytest.mark.parametrize(
    "cpf",
    [
        "12345678900",
        "５２９９８２２４７２５",
        "529.982.24725",
    ],
)
def test_register_rejects_invalid_cpf_without_creating_records(
    registration_client, cpf
):
    client, db = registration_client

    response = _register(client, cpf)

    assert response.status_code == 422
    assert response.json()["detail"] == "CPF inválido."
    assert cpf not in response.text
    assert db.scalar(select(func.count()).select_from(User)) == 0
    assert db.scalar(select(func.count()).select_from(Notification)) == 0
    assert db.scalar(select(func.count()).select_from(UserSession)) == 0


def test_register_detects_duplicate_cpf_in_plain_and_formatted_forms(
    registration_client,
):
    client, db = registration_client

    first = _register(client, VALID_CPF)
    assert first.status_code == 200

    second = client.post(
        "/auth/register",
        json={
            "name": "CPF Duplicate User",
            "email": "cpf-duplicate@example.test",
            "cpf": FORMATTED_CPF,
            "phone": None,
            "password": "StrongTestPassword123!",
            "accept_terms": True,
        },
    )

    assert second.status_code == 409
    assert second.json()["detail"] == "CPF já cadastrado."
    assert db.scalar(select(func.count()).select_from(User)) == 1
    assert db.scalar(select(func.count()).select_from(Notification)) == 1
    assert db.scalar(select(func.count()).select_from(UserSession)) == 1


def test_invalid_cpf_response_does_not_echo_sensitive_value(registration_client):
    client, _db = registration_client

    response = _register(client, OTHER_VALID_CPF[:-1] + "0")

    assert response.status_code == 422
    assert response.json()["detail"] == "CPF inválido."
    assert OTHER_VALID_CPF not in response.text
