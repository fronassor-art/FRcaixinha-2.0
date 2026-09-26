"""Aggregate-only, read-only legacy identity preflight tests."""

from collections.abc import Iterator
from dataclasses import asdict, fields
from typing import get_type_hints

import pytest
import sqlalchemy as sa
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import User
from app.services.legacy_identity_preflight import (
    LegacyIdentityPreflightReport,
    evaluate_legacy_identity_preflight,
)
from app.services.privacy_v045 import _privacy_cpf_tombstone


CPF_A = "52998224725"
CPF_A_FORMATTED = "529.982.247-25"
CPF_B = "11144477735"
CPF_C = "12345678909"


@pytest.fixture()
def identity_db(tmp_path) -> Iterator[Session]:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'identity-preflight.sqlite'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        yield db
    engine.dispose()


def _user(user_id: int, *, cpf: str, email: str, active: bool = True) -> User:
    return User(
        id=user_id,
        name=f"Synthetic User {user_id}",
        email=email,
        cpf=cpf,
        password_hash="test-only",
        is_active=active,
    )


def test_cpf_categories_tombstones_and_three_user_logical_collision(identity_db):
    identity_db.add_all(
        [
            _user(1, cpf=CPF_A, email="one@example.com"),
            _user(2, cpf=CPF_A_FORMATTED, email="two@example.com"),
            _user(3, cpf=f" {CPF_A} ", email="three@example.com"),
            _user(4, cpf=CPF_B, email="four@example.com"),
            _user(5, cpf="invalid-cpf", email="five@example.com"),
            _user(6, cpf="inactive-unknown", email="six@example.com", active=False),
            _user(
                7,
                cpf=_privacy_cpf_tombstone(7),
                email="seven@example.com",
                active=False,
            ),
            _user(8, cpf=_privacy_cpf_tombstone(18), email="eight@example.com", active=False),
            _user(9, cpf=_privacy_cpf_tombstone(9), email="nine@example.com"),
        ]
    )
    identity_db.commit()

    report = evaluate_legacy_identity_preflight(identity_db)

    assert report.total_users == 9
    assert report.active_users == 6
    assert report.cpf_canonical_valid == 2
    assert report.cpf_formatted_valid_legacy == 1
    assert report.cpf_invalid_active == 1
    assert report.cpf_privacy_tombstone_valid == 1
    assert report.cpf_privacy_tombstone_malformed == 2
    assert report.cpf_unknown_legacy == 2
    assert report.cpf_logical_collision_groups == 1
    assert report.cpf_logical_collision_users == 3


def test_email_categories_case_collisions_and_privacy_markers(identity_db):
    identity_db.add_all(
        [
            _user(1, cpf=CPF_A, email="canonical@example.com"),
            _user(2, cpf=CPF_B, email="MixedCase@example.com"),
            _user(3, cpf=CPF_C, email="CaseCollision@example.com"),
            _user(4, cpf="legacy-four", email="casecollision@example.com"),
            _user(5, cpf="legacy-five", email="CASECOLLISION@example.com"),
            _user(
                6,
                cpf="legacy-six",
                email="anon-6-012345abcdef@anon.invalid",
                active=False,
            ),
            _user(
                7,
                cpf="legacy-seven",
                email="anon-70-012345abcdef@anon.invalid",
                active=False,
            ),
            _user(
                8,
                cpf="legacy-eight",
                email="anon-8-not12@anon.invalid",
                active=False,
            ),
            _user(
                9,
                cpf="legacy-nine",
                email="anon-9-012345abcdef@anon.invalid",
                active=True,
            ),
            _user(10, cpf="legacy-ten", email="not-an-email"),
        ]
    )
    identity_db.commit()

    report = evaluate_legacy_identity_preflight(identity_db)

    assert report.total_users == 10
    assert report.active_users == 7
    assert report.email_canonical == 2
    assert report.email_mixed_case_legacy == 3
    assert report.email_privacy_tombstone_compatible == 1
    assert report.email_privacy_tombstone_malformed == 3
    assert report.email_invalid_or_unknown_legacy == 1
    assert report.email_case_collision_groups == 1
    assert report.email_case_collision_users == 3


def test_normal_anon_prefix_emails_are_classified_normally(identity_db):
    identity_db.add_all(
        [
            _user(1, cpf=CPF_A, email="anon-silva@gmail.com"),
            _user(2, cpf=CPF_B, email="Anon-Silva@Example.com"),
            _user(3, cpf=CPF_C, email="anon-user@example.com"),
            _user(4, cpf="legacy-four", email="ANON-USER@example.com"),
        ]
    )
    identity_db.commit()

    report = evaluate_legacy_identity_preflight(identity_db)

    assert report.email_canonical == 2
    assert report.email_mixed_case_legacy == 2
    assert report.email_privacy_tombstone_compatible == 0
    assert report.email_privacy_tombstone_malformed == 0
    assert report.email_case_collision_groups == 1
    assert report.email_case_collision_users == 2


def test_report_contains_only_integer_aggregate_fields_and_no_identity_values(
    identity_db,
):
    cpf_marker = "529.982.247-25"
    email_marker = "private-marker@example.com"
    identity_db.add(
        _user(1, cpf=cpf_marker, email=email_marker)
    )
    identity_db.commit()

    report = evaluate_legacy_identity_preflight(identity_db)
    values = asdict(report)

    assert isinstance(report, LegacyIdentityPreflightReport)
    report_types = get_type_hints(LegacyIdentityPreflightReport)
    assert all(report_types[field.name] is int for field in fields(report))
    assert all(type(value) is int for value in values.values())
    assert cpf_marker not in repr(report)
    assert email_marker not in repr(report)
    assert all(not isinstance(value, (list, tuple, dict, set)) for value in values.values())
    assert not any(isinstance(value, User) for value in values.values())


def test_preflight_executes_no_dml_or_transaction_finalization(identity_db):
    identity_db.add(_user(1, cpf=CPF_A, email="readonly@example.com"))
    identity_db.commit()
    dml_statements = []
    select_statements = []
    transaction_finalizations = []

    def capture_sql(_connection, _cursor, statement, _parameters, _context, _many):
        command = statement.lstrip().split(None, 1)[0].upper()
        if command in {"INSERT", "UPDATE", "DELETE"}:
            dml_statements.append(command)
        if command == "SELECT":
            select_statements.append(statement.lower())

    def capture_commit(_session):
        transaction_finalizations.append("commit")

    def capture_rollback(_session):
        transaction_finalizations.append("rollback")

    event.listen(identity_db.bind, "before_cursor_execute", capture_sql)
    event.listen(identity_db, "after_commit", capture_commit)
    event.listen(identity_db, "after_rollback", capture_rollback)
    try:
        report = evaluate_legacy_identity_preflight(identity_db)
        assert report.total_users == 1
    finally:
        event.remove(identity_db.bind, "before_cursor_execute", capture_sql)
        event.remove(identity_db, "after_commit", capture_commit)
        event.remove(identity_db, "after_rollback", capture_rollback)

    assert dml_statements == []
    assert len(select_statements) == 1
    for column in ("users.id", "users.cpf", "users.email", "users.is_active"):
        assert column in select_statements[0]
    for column in ("users.name", "users.phone", "users.password_hash"):
        assert column not in select_statements[0]
    assert transaction_finalizations == []
    assert identity_db.is_active
    assert not identity_db.new
    assert not identity_db.dirty
    assert identity_db.scalar(select(func.count()).select_from(User)) == 1


def test_preflight_does_not_autoflush_pending_user(identity_db):
    identity_db.add(_user(1, cpf=CPF_A, email="persisted@example.com"))
    identity_db.commit()
    pending = _user(2, cpf=CPF_B, email="pending@example.com")
    pending.id = None
    identity_db.add(pending)
    dml_statements = []

    def capture_sql(_connection, _cursor, statement, _parameters, _context, _many):
        command = statement.lstrip().split(None, 1)[0].upper()
        if command in {"INSERT", "UPDATE", "DELETE"}:
            dml_statements.append(command)

    event.listen(identity_db.bind, "before_cursor_execute", capture_sql)
    try:
        report = evaluate_legacy_identity_preflight(identity_db)
    finally:
        event.remove(identity_db.bind, "before_cursor_execute", capture_sql)

    assert report.total_users == 1
    assert pending in identity_db.new
    assert pending.id is None
    assert dml_statements == []
    assert identity_db.is_active
    identity_db.rollback()
