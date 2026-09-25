"""Security, normalization and domain tests for member PIX destinations."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Barrier

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import AuditLog, Group, Member, MemberPayoutDestination, User
from app.services.member_payout_destination import (
    PayoutDestinationConflict,
    create_unverified_destination,
    get_active_destination,
    replace_destination,
    revoke_destination,
)
from app.services.payout_destination_crypto import (
    PayoutDestinationCryptoError,
    decrypt_payout_destination,
    encrypt_payout_destination,
    mask_payout_destination,
)
from app.services.payout_destination_validation import (
    PayoutDestinationValidationError,
    normalize_key_type,
    normalize_payout_destination,
)


PLAIN_CPF = "529.982.247-25"
CPF_NORMALIZED = "52998224725"
PHONE = "+5511998765432"
EMAIL = "Member.Example@example.com"
EVP = "550E8400-E29B-41D4-A716-446655440000"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(
        settings, "payout_destination_encryption_key", Fernet.generate_key().decode("ascii")
    )
    engine = sa.create_engine(
        f"sqlite:///{tmp_path / 'payout-destinations.sqlite'}",
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
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("a377b4r2_migration", migration_path)
    migration = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        migration._create_sqlite_guards(connection)

    with Session(engine, expire_on_commit=False) as session:
        session.add_all([
            User(id=1, name="Destination Admin", email="admin@destination.test",
                 cpf="10000000001", password_hash="unused", role="ADMIN"),
            User(id=2, name="Destination Member", email="member@destination.test",
                 cpf="10000000002", password_hash="unused"),
            Group(id=1, name="Destination test group"),
        ])
        session.flush()
        session.add(Member(id=1, user_id=2, group_id=1))
        session.commit()
        yield session
        session.rollback()
    engine.dispose()


@pytest.mark.parametrize(
    ("kind", "raw", "expected"),
    [
        ("CPF", PLAIN_CPF, CPF_NORMALIZED),
        ("CPF", CPF_NORMALIZED, CPF_NORMALIZED),
        ("PHONE", PHONE, PHONE),
        ("PHONE", "+551132345678", "+551132345678"),
        ("EMAIL", EMAIL, EMAIL),
        ("EVP", EVP, EVP.lower()),
    ],
)
def test_normalizes_supported_pix_keys(kind, raw, expected):
    assert normalize_payout_destination(kind, raw) == expected


@pytest.mark.parametrize(
    ("kind", "raw"),
    [
        ("CPF", "111.111.111-11"),
        ("CPF", "12345678900"),
        ("CPF", "52x.982.247-25"),
        ("PHONE", "11998765432"),
        ("PHONE", "+55119987"),
        ("PHONE", "+5511668765432"),
        ("EMAIL", "not-an-email"),
        ("EVP", "not-a-uuid"),
        ("CNPJ", "11222333000181"),
        ("UNKNOWN", "opaque"),
    ],
)
def test_rejects_invalid_or_unsupported_pix_keys(kind, raw):
    with pytest.raises(PayoutDestinationValidationError):
        normalize_payout_destination(kind, raw)


def test_normalizes_key_type_case_and_rejects_non_string():
    assert normalize_key_type(" email ") == "EMAIL"
    with pytest.raises(PayoutDestinationValidationError):
        normalize_key_type(None)


def test_fernet_roundtrip_is_randomized_and_masks_every_key_type(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(
        settings, "payout_destination_encryption_key", Fernet.generate_key().decode("ascii")
    )
    encrypted_one = encrypt_payout_destination(CPF_NORMALIZED)
    encrypted_two = encrypt_payout_destination(CPF_NORMALIZED)
    assert encrypted_one != CPF_NORMALIZED
    assert encrypted_one != encrypted_two
    assert decrypt_payout_destination(encrypted_one) == CPF_NORMALIZED

    for kind, raw in (("CPF", CPF_NORMALIZED), ("PHONE", PHONE),
                      ("EMAIL", EMAIL), ("EVP", EVP.lower())):
        masked = mask_payout_destination(kind, raw)
        assert raw not in masked
        assert masked != raw


@pytest.mark.parametrize(
    ("configured_key", "ciphertext"),
    [(None, "gAAAA-invalid"), ("not-a-fernet-key", "gAAAA-invalid")],
)
def test_missing_or_invalid_encryption_key_fails_without_exposing_value(
    monkeypatch, configured_key, ciphertext,
):
    from app.core.config import settings

    sensitive_value = "private-pix-key-material"
    monkeypatch.setattr(settings, "payout_destination_encryption_key", configured_key)
    with pytest.raises(PayoutDestinationCryptoError) as caught:
        encrypt_payout_destination(sensitive_value)
    assert sensitive_value not in str(caught.value)
    with pytest.raises(PayoutDestinationCryptoError):
        decrypt_payout_destination(ciphertext)


def test_wrong_encryption_key_does_not_decrypt(monkeypatch):
    from app.core.config import settings

    original_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(settings, "payout_destination_encryption_key", original_key)
    encrypted = encrypt_payout_destination(CPF_NORMALIZED)
    monkeypatch.setattr(
        settings, "payout_destination_encryption_key", Fernet.generate_key().decode("ascii")
    )
    with pytest.raises(PayoutDestinationCryptoError) as caught:
        decrypt_payout_destination(encrypted)
    assert CPF_NORMALIZED not in str(caught.value)


def test_create_stores_ciphertext_unverified_and_audits_only_safe_fields(db):
    row = create_unverified_destination(
        db, member_id=1, key_type="CPF", value=PLAIN_CPF, actor_id=1,
    )
    db.flush()
    db.refresh(row)

    assert row.version == 1
    assert row.verification_status == "UNVERIFIED"
    assert row.encrypted_value != CPF_NORMALIZED
    assert decrypt_payout_destination(row.encrypted_value) == CPF_NORMALIZED
    assert row.masked_value == "***.***.***-25"
    assert CPF_NORMALIZED not in row.encrypted_value

    persisted = db.execute(
        text("SELECT encrypted_value, masked_value FROM member_payout_destinations")
    ).one()
    assert CPF_NORMALIZED not in persisted.encrypted_value
    assert CPF_NORMALIZED not in persisted.masked_value
    details = db.query(AuditLog).one().details
    assert "PAYOUT_DESTINATION_CREATED" == db.query(AuditLog).one().action
    assert CPF_NORMALIZED not in details
    assert row.encrypted_value not in details
    assert "529.982.247-25" not in details


def test_replace_revokes_old_version_and_new_version_is_unverified(db):
    old = create_unverified_destination(
        db, member_id=1, key_type="CPF", value=PLAIN_CPF, actor_id=1,
    )
    db.flush()
    old_id = old.id

    new = replace_destination(
        db, member_id=1, key_type="EMAIL", value=EMAIL, actor_id=1,
    )
    db.flush()
    db.refresh(old)

    assert new.version == 2
    assert new.verification_status == "UNVERIFIED"
    assert old.verification_status == "REVOKED"
    assert old.revoked_at is not None and old.revoked_by == 1
    assert get_active_destination(db, member_id=1).id == new.id
    assert db.query(MemberPayoutDestination).filter_by(member_id=1).count() == 2
    events = db.query(AuditLog).order_by(AuditLog.id).all()
    assert [event.action for event in events] == [
        "PAYOUT_DESTINATION_CREATED",
        "PAYOUT_DESTINATION_REVOKED",
        "PAYOUT_DESTINATION_CREATED",
    ]
    assert db.get(MemberPayoutDestination, old_id) is old


def test_revoke_preserves_history_and_no_artificial_verify_service_exists(db):
    from app.services import member_payout_destination as service

    row = create_unverified_destination(
        db, member_id=1, key_type="EVP", value=EVP, actor_id=1,
    )
    db.flush()
    row_id = row.id
    revoked = revoke_destination(db, destination_id=row.id, actor_id=1)
    assert revoked.verification_status == "REVOKED"
    assert db.get(MemberPayoutDestination, row_id) is not None
    assert not hasattr(service, "mark_verified")
    assert not hasattr(service, "verify_destination")
    assert [item.action for item in db.query(AuditLog).order_by(AuditLog.id)] == [
        "PAYOUT_DESTINATION_CREATED", "PAYOUT_DESTINATION_REVOKED",
    ]


def test_create_replace_and_revoke_audits_rollback_with_domain_transaction(db):
    create_unverified_destination(
        db, member_id=1, key_type="PHONE", value=PHONE, actor_id=1,
    )
    db.rollback()
    assert db.query(MemberPayoutDestination).count() == 0
    assert db.query(AuditLog).count() == 0


def test_member_actor_and_active_destination_conflicts_are_explicit(db):
    with pytest.raises(PayoutDestinationConflict, match="Member not found"):
        create_unverified_destination(
            db, member_id=999, key_type="CPF", value=PLAIN_CPF, actor_id=1,
        )
    with pytest.raises(PayoutDestinationConflict, match="actor not found"):
        create_unverified_destination(
            db, member_id=1, key_type="CPF", value=PLAIN_CPF, actor_id=999,
        )
    first = create_unverified_destination(
        db, member_id=1, key_type="CPF", value=PLAIN_CPF, actor_id=1,
    )
    db.flush()
    with pytest.raises(PayoutDestinationConflict, match="already has an active"):
        create_unverified_destination(
            db, member_id=1, key_type="EMAIL", value=EMAIL, actor_id=1,
        )
    assert get_active_destination(db, member_id=1).id == first.id


def test_orm_and_database_block_version_mutation_deletion_and_fake_verification(db):
    row = create_unverified_destination(
        db, member_id=1, key_type="CPF", value=PLAIN_CPF, actor_id=1,
    )
    db.flush()
    db.commit()
    row_id = row.id
    original_ciphertext = row.encrypted_value

    row.encrypted_value = "plaintext"
    with pytest.raises(RuntimeError, match="campos da versão são imutáveis"):
        db.flush()
    db.rollback()
    row = db.get(MemberPayoutDestination, row_id)
    assert row.encrypted_value == original_ciphertext

    with pytest.raises(SQLAlchemyError):
        db.execute(
            text("UPDATE member_payout_destinations SET encrypted_value='plaintext' WHERE id=:id"),
            {"id": row_id},
        )
    db.rollback()
    with pytest.raises(SQLAlchemyError):
        db.execute(
            text("UPDATE member_payout_destinations SET verification_status='VERIFIED', verified_at=:now, verified_by=1 WHERE id=:id"),
            {"id": row_id, "now": datetime.now(timezone.utc)},
        )
    db.rollback()
    with pytest.raises(SQLAlchemyError):
        db.execute(
            text("DELETE FROM member_payout_destinations WHERE id=:id"), {"id": row_id}
        )
    db.rollback()
    assert db.get(MemberPayoutDestination, row_id) is not None


@pytest.mark.parametrize(
    ("version", "key_type", "status"),
    [(0, "CPF", "UNVERIFIED"), (1, "CNPJ", "UNVERIFIED"),
     (1, "CPF", "PENDING")],
)
def test_database_rejects_invalid_version_key_type_and_status(db, version, key_type, status):
    with pytest.raises(SQLAlchemyError):
        db.execute(
            text("""
                INSERT INTO member_payout_destinations
                    (member_id, version, key_type, encrypted_value, masked_value,
                     verification_status, created_at, created_by)
                VALUES (1, :version, :key_type, 'ciphertext', 'masked', :status, :created, 1)
            """),
            {"version": version, "key_type": key_type, "status": status,
             "created": datetime.now(timezone.utc)},
        )
    db.rollback()
    assert db.query(MemberPayoutDestination).count() == 0


def test_database_rejects_second_active_and_insert_as_verified(db):
    create_unverified_destination(
        db, member_id=1, key_type="CPF", value=PLAIN_CPF, actor_id=1,
    )
    db.flush()
    db.commit()
    with pytest.raises(SQLAlchemyError):
        db.execute(
            text("""
                INSERT INTO member_payout_destinations
                    (member_id, version, key_type, encrypted_value, masked_value,
                     verification_status, created_at, created_by)
                VALUES (1, 2, 'EVP', 'ciphertext', 'masked', 'UNVERIFIED', :created, 1)
            """), {"created": datetime.now(timezone.utc)},
        )
    db.rollback()
    with pytest.raises(SQLAlchemyError):
        db.execute(
            text("""
                INSERT INTO member_payout_destinations
                    (member_id, version, key_type, encrypted_value, masked_value,
                     verification_status, created_at, created_by, verified_at, verified_by)
                VALUES (1, 2, 'EVP', 'ciphertext', 'masked', 'VERIFIED', :created, 1, :created, 1)
            """), {"created": datetime.now(timezone.utc)},
        )
    db.rollback()
    assert db.query(MemberPayoutDestination).filter_by(verification_status="UNVERIFIED").count() == 1


def test_sqlite_partial_unique_index_has_matching_predicate(db):
    index = next(
        item for item in sa.inspect(db.bind).get_indexes("member_payout_destinations")
        if item["name"] == "uq_mpd_one_active_member"
    )
    assert bool(index["unique"])
    assert "verification_status" in index["dialect_options"]["sqlite_where"].text


def test_destination_encryption_key_uses_existing_file_secret_reader(tmp_path, monkeypatch):
    from app.core.config import _read_file_env

    secret_file = tmp_path / "payout-destination.key"
    secret_file.write_text("dedicated-secret\n", encoding="utf-8")
    monkeypatch.setenv("PAYOUT_DESTINATION_ENCRYPTION_KEY_FILE", str(secret_file))
    assert _read_file_env("payout_destination_encryption_key") == "dedicated-secret"


def test_concurrent_first_registration_cannot_create_two_active_destinations(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(
        settings, "payout_destination_encryption_key", Fernet.generate_key().decode("ascii")
    )
    engine = sa.create_engine(
        f"sqlite:///{tmp_path / 'payout-destination-concurrency.sqlite'}",
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
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("a377b4r2_concurrency_migration", migration_path)
    migration = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        migration._create_sqlite_guards(connection)
    with Session(engine) as session:
        session.add_all([
            User(id=1, name="Admin", email="admin@concurrency.test", cpf="20000000001",
                 password_hash="unused", role="ADMIN"),
            User(id=2, name="Member", email="member@concurrency.test", cpf="20000000002",
                 password_hash="unused"),
            Group(id=1, name="Concurrency group"),
        ])
        session.flush()
        session.add(Member(id=1, user_id=2, group_id=1))
        session.commit()

    barrier = Barrier(2)

    def register(value):
        with Session(engine, expire_on_commit=False) as session:
            barrier.wait(timeout=10)
            try:
                create_unverified_destination(
                    session, member_id=1, key_type="EMAIL", value=value, actor_id=1,
                )
                session.commit()
                return "created"
            except PayoutDestinationConflict:
                session.rollback()
                return "conflict"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(register, ("first@example.com", "second@example.com")))
        assert sorted(results) == ["conflict", "created"]
        with Session(engine) as session:
            assert session.query(MemberPayoutDestination).count() == 1
            assert session.query(MemberPayoutDestination).filter(
                MemberPayoutDestination.verification_status != "REVOKED"
            ).count() == 1
    finally:
        engine.dispose()
