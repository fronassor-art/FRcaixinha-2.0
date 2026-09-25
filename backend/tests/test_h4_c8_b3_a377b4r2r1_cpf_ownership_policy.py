"""Read-only CPF ownership eligibility policy tests (A3.77B4-R4.2-R1)."""

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import importlib.util

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import AuditLog, Group, Member, MemberPayoutDestination, User
from app.services.member_payout_destination import (
    create_unverified_destination,
    revoke_destination,
)
from app.services.payout_destination_crypto import (
    encrypt_payout_destination,
)
from app.services.payout_destination_ownership_policy import (
    PayoutDestinationOwnershipEligibility,
    evaluate_payout_destination_ownership_eligibility,
)
from app.services.payout_destination_validation import normalize_payout_destination


MEMBER_CPF = "52998224725"
FORMATTED_MEMBER_CPF = "529.982.247-25"
OTHER_VALID_CPF = "11144477735"
PHONE = "+5511998765432"
EMAIL = "member@example.com"
EVP = "550e8400-e29b-41d4-a716-446655440000"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(
        settings,
        "payout_destination_encryption_key",
        Fernet.generate_key().decode("ascii"),
    )
    engine = sa.create_engine(
        f"sqlite:///{tmp_path / 'cpf-ownership-policy.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    migration_path = (
        Path(__file__).parents[1]
        / "alembic/versions/0101_member_payout_destination_a377b4r2.py"
    )
    spec = importlib.util.spec_from_file_location("a377b4r2_ownership_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        migration._create_sqlite_guards(connection)

    with Session(engine, expire_on_commit=False) as session:
        session.add_all([
            User(
                id=1,
                name="Policy Actor",
                email="actor@ownership.test",
                cpf="10000000001",
                password_hash="unused",
                role="ADMIN",
            ),
            User(
                id=2,
                name="CPF Owner",
                email="member@ownership.test",
                cpf=FORMATTED_MEMBER_CPF,
                password_hash="unused",
            ),
            Group(id=1, name="Ownership test group"),
        ])
        session.flush()
        session.add(Member(id=1, user_id=2, group_id=1))
        session.commit()
        yield session
        session.rollback()
    engine.dispose()


def _create_destination(db, key_type="CPF", value=FORMATTED_MEMBER_CPF):
    row = create_unverified_destination(
        db,
        member_id=1,
        key_type=key_type,
        value=value,
        actor_id=1,
    )
    db.flush()
    return row


def test_member_not_found(db):
    result = evaluate_payout_destination_ownership_eligibility(db, member_id=999)
    assert result == PayoutDestinationOwnershipEligibility(
        False, 999, None, None, "MEMBER_NOT_FOUND",
    )


def test_missing_user_link_fails_closed_without_invalid_foreign_key(db, monkeypatch):
    original_get = db.get

    def get(model, identity, *args, **kwargs):
        if model is User and identity == 2:
            return None
        return original_get(model, identity, *args, **kwargs)

    monkeypatch.setattr(db, "get", get)
    result = evaluate_payout_destination_ownership_eligibility(db, member_id=1)
    assert result.reason_code == "MEMBER_USER_NOT_FOUND"


def test_missing_active_destination_and_revoked_history_are_not_eligible(db):
    missing = evaluate_payout_destination_ownership_eligibility(db, member_id=1)
    assert missing.reason_code == "DESTINATION_MISSING"

    row = _create_destination(db)
    revoke_destination(db, destination_id=row.id, actor_id=1)
    revoked = evaluate_payout_destination_ownership_eligibility(db, member_id=1)
    assert revoked.reason_code == "DESTINATION_MISSING"
    assert revoked.destination_id is None


def test_valid_equal_cpf_is_eligible_but_stays_unverified(db):
    assert normalize_payout_destination("CPF", FORMATTED_MEMBER_CPF) == MEMBER_CPF
    destination = _create_destination(db)

    result = evaluate_payout_destination_ownership_eligibility(db, member_id=1)

    assert result.eligible is True
    assert result.reason_code is None
    assert result.member_id == 1
    assert result.destination_id == destination.id
    assert result.key_type == "CPF"
    assert destination.verification_status == "UNVERIFIED"
    assert destination.verified_at is None
    assert destination.verified_by is None


def test_valid_but_different_cpf_returns_mismatch_without_disclosing_values(db):
    assert normalize_payout_destination("CPF", OTHER_VALID_CPF) == OTHER_VALID_CPF
    destination = _create_destination(db, value=OTHER_VALID_CPF)
    result = evaluate_payout_destination_ownership_eligibility(db, member_id=1)

    assert result.eligible is False
    assert result.reason_code == "DESTINATION_CPF_MISMATCH"
    rendered = repr(asdict(result))
    assert MEMBER_CPF not in rendered
    assert OTHER_VALID_CPF not in rendered
    assert FORMATTED_MEMBER_CPF not in rendered
    assert destination.encrypted_value not in rendered


def test_invalid_member_cpf_fails_closed(db):
    db.execute(
        text("UPDATE users SET cpf=:cpf WHERE id=2"),
        {"cpf": "12345678900"},
    )
    db.commit()
    _create_destination(db)

    result = evaluate_payout_destination_ownership_eligibility(db, member_id=1)

    assert result.reason_code == "MEMBER_CPF_INVALID"


def test_invalid_decrypted_destination_cpf_fails_closed(db):
    row = MemberPayoutDestination(
        member_id=1,
        version=1,
        key_type="CPF",
        encrypted_value=encrypt_payout_destination("11111111111"),
        masked_value="***.***.***-11",
        verification_status="UNVERIFIED",
        created_at=datetime.now(timezone.utc),
        created_by=1,
    )
    db.add(row)
    db.flush()

    result = evaluate_payout_destination_ownership_eligibility(db, member_id=1)

    assert result.reason_code == "DESTINATION_CPF_INVALID"
    assert "11111111111" not in repr(result)


def test_adulterated_ciphertext_fails_closed_without_leaking_cpf(db):
    destination = MemberPayoutDestination(
        member_id=1,
        version=1,
        key_type="CPF",
        encrypted_value="not-a-fernet-token",
        masked_value="***.***.***-25",
        verification_status="UNVERIFIED",
        created_at=datetime.now(timezone.utc),
        created_by=1,
    )
    db.add(destination)
    db.flush()

    result = evaluate_payout_destination_ownership_eligibility(db, member_id=1)

    assert result.reason_code == "DESTINATION_DECRYPTION_FAILED"
    assert MEMBER_CPF not in repr(result)


@pytest.mark.parametrize(
    ("key_type", "value"),
    [("PHONE", PHONE), ("EMAIL", EMAIL), ("EVP", EVP)],
)
def test_non_cpf_types_are_not_eligible_but_remain_stored(db, key_type, value):
    destination = _create_destination(db, key_type=key_type, value=value)

    result = evaluate_payout_destination_ownership_eligibility(db, member_id=1)

    assert result.eligible is False
    assert result.reason_code == "UNSUPPORTED_KEY_TYPE"
    assert result.destination_id == destination.id
    assert result.key_type == key_type
    assert destination.key_type == key_type
    assert destination.verification_status == "UNVERIFIED"


def test_non_unverified_destination_is_not_eligible(db, monkeypatch):
    destination = _create_destination(db)
    monkeypatch.setattr(
        "app.services.payout_destination_ownership_policy.get_active_destination",
        lambda _db, *, member_id: SimpleNamespace(
            id=destination.id,
            key_type="CPF",
            verification_status="VERIFIED",
        ),
    )

    result = evaluate_payout_destination_ownership_eligibility(db, member_id=1)

    assert result.reason_code == "DESTINATION_NOT_UNVERIFIED"


def test_policy_is_read_only_and_does_not_create_audit_or_verify_destination(db):
    destination = _create_destination(db)
    db.commit()
    destination = db.get(MemberPayoutDestination, destination.id)
    before = (
        db.query(MemberPayoutDestination).count(),
        db.query(AuditLog).count(),
        destination.member_id,
        destination.version,
        destination.key_type,
        destination.encrypted_value,
        destination.masked_value,
        destination.verification_status,
        destination.verified_at,
        destination.verified_by,
    )
    dml = []

    def capture_dml(_conn, _cursor, statement, _parameters, _context, _many):
        verb = statement.lstrip().split(None, 1)[0].upper()
        if verb in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            dml.append(verb)

    event.listen(db.bind, "before_cursor_execute", capture_dml)
    try:
        result = evaluate_payout_destination_ownership_eligibility(db, member_id=1)
    finally:
        event.remove(db.bind, "before_cursor_execute", capture_dml)

    db.refresh(destination)
    after = (
        db.query(MemberPayoutDestination).count(),
        db.query(AuditLog).count(),
        destination.member_id,
        destination.version,
        destination.key_type,
        destination.encrypted_value,
        destination.masked_value,
        destination.verification_status,
        destination.verified_at,
        destination.verified_by,
    )
    assert result.eligible is True
    assert dml == []
    assert after == before
    assert destination.verification_status == "UNVERIFIED"
    assert destination.verified_at is None
    assert destination.verified_by is None
