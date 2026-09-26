"""SQLite migration and persistence contracts for payout verification evidence."""

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Group,
    Member,
    MemberPayoutDestination,
    PayoutDestinationVerificationAttempt,
    PayoutDestinationVerificationEvidence,
    User,
)


ROOT = Path(__file__).resolve().parents[1]
PREVIOUS = "0101_member_payout_destination_a377b4r2"
REVISION = "0102_payout_verification_evidence_a377b4r3"
ACTIVE_INDEX = "uq_pdva_one_active_destination"
FINAL_INDEX = "uq_pdve_one_final_per_attempt"
BASELINE = "16655cb6c78b975ebd7f7a239f3ffb10484fda03"
DIGEST = "sha256:" + "a" * 64
NOW = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)


def _alembic(database, *args):
    env = os.environ.copy()
    env["A377B4F4_TEST_URL"] = f"sqlite:///{database}"
    script = r"""
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
from alembic import command
from alembic.config import Config
root = Path.cwd()
config = Config(str(root / 'alembic.ini'))
config.set_main_option('script_location', str(root / 'alembic'))
fake = ModuleType('app.core.config')
fake.settings = SimpleNamespace(database_url=os.environ['A377B4F4_TEST_URL'])
with patch.dict(sys.modules, {'app.core.config': fake}):
    command.__dict__[sys.argv[1]](config, sys.argv[2])
"""
    operation = args[0]
    revision = args[1]
    result = subprocess.run(
        [sys.executable, "-c", script, operation, revision],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    result.check_returncode()


@pytest.fixture(scope="module")
def database(tmp_path_factory):
    path = tmp_path_factory.mktemp("payout-verification") / "foundation.sqlite"
    _alembic(path, "upgrade", REVISION)
    engine = sa.create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    with Session(engine) as session:
        session.add_all([
            User(id=81001, name="Fixture Admin", email="admin@verification.test", cpf="10000000001", password_hash="unused", role="ADMIN"),
            User(id=81002, name="Fixture Member", email="member@verification.test", cpf="10000000002", password_hash="unused"),
            Group(id=81001, name="Verification test group"),
        ])
        session.flush()
        session.add(Member(id=81001, user_id=81002, group_id=81001))
        session.flush()
        session.add(MemberPayoutDestination(
            id=81001, member_id=81001, version=1, key_type="CPF",
            encrypted_value="fixture-encrypted", masked_value="***0001",
            verification_status="UNVERIFIED", created_at=NOW, created_by=81001,
        ))
        session.commit()

    yield engine
    engine.dispose()


def _insert_destination(engine, destination_id, *, version=1, key_type="CPF", status="UNVERIFIED"):
    owner_id = 82000 + destination_id
    with Session(engine) as session:
        session.add(User(
            id=owner_id, name=f"Fixture {destination_id}",
            email=f"member-{destination_id}@verification.test",
            cpf=f"{owner_id:011d}", password_hash="unused",
        ))
        session.flush()
        session.add(Member(id=destination_id, user_id=owner_id, group_id=81001))
        session.commit()
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO member_payout_destinations
                (id, member_id, version, key_type, encrypted_value, masked_value,
                 verification_status, created_at, created_by)
            VALUES (:id, :member_id, :version, :key_type, 'fixture', '***', 'UNVERIFIED',
                    '2026-01-02 03:04:00', :created_by)
        """), {"id": destination_id, "member_id": destination_id, "version": version,
               "key_type": key_type, "created_by": owner_id})
        if status == "REVOKED":
            connection.execute(text("""
                UPDATE member_payout_destinations
                SET verification_status='REVOKED', revoked_at='2026-01-02 03:04:00', revoked_by=81001
                WHERE id=:id
            """), {"id": destination_id})


def _insert_attempt(connection, *, destination_id=81001, destination_version=1,
                    key_type="CPF", state="REQUESTED", attempt_id="attempt-1",
                    idempotency_key="idem-1"):
    connection.execute(text("""
        INSERT INTO payout_destination_verification_attempts
            (attempt_id, idempotency_key, destination_id, destination_version,
             key_type, provider_name, state, requested_at, requested_by, created_at)
        VALUES (:attempt_id, :idempotency_key, :destination_id, :destination_version,
                :key_type, 'provider-neutral', :state, :requested_at, 81001, :created_at)
    """), {
        "attempt_id": attempt_id, "idempotency_key": idempotency_key,
        "destination_id": destination_id, "destination_version": destination_version,
        "key_type": key_type, "state": state,
        "requested_at": "2026-01-02 03:04:00+00:00", "created_at": "2026-01-02 03:04:00+00:00",
    })


def _insert_evidence(connection, attempt_pk, *, sequence=1, state="PENDING", outcome=None,
                     freshness="CURRENT", digest=DIGEST, authenticity="NOT_CHECKED",
                     method=None, checked_at=None, provider_event_id=None):
    connection.execute(text("""
        INSERT INTO payout_destination_verification_evidence
            (verification_attempt_id, sequence, result_state, outcome, reason_code,
             provider_request_id, provider_response_id, provider_event_id,
             provider_timestamp, received_at, authenticity_status, authenticity_method,
             authenticity_checked_at, freshness, evidence_digest, created_at)
        VALUES (:attempt_pk, :sequence, :state, :outcome, NULL,
                NULL, NULL, :provider_event_id, NULL, :received_at, :authenticity,
                :method, :checked_at, :freshness, :digest, :created_at)
    """), {
        "attempt_pk": attempt_pk, "sequence": sequence, "state": state,
        "outcome": outcome, "provider_event_id": provider_event_id,
        "received_at": "2026-01-02 03:04:00+00:00", "authenticity": authenticity,
        "method": method, "checked_at": checked_at, "freshness": freshness,
        "digest": digest, "created_at": "2026-01-02 03:04:00+00:00",
    })


def _attempt_pk(connection, attempt_id):
    return connection.execute(text(
        "SELECT id FROM payout_destination_verification_attempts WHERE attempt_id=:attempt_id"
    ), {"attempt_id": attempt_id}).scalar_one()


def test_real_0101_to_0102_upgrade_creates_tables_and_model_contract(database):
    tables = set(inspect(database).get_table_names())
    assert "payout_destination_verification_attempts" in tables
    assert "payout_destination_verification_evidence" in tables
    assert "member_payout_destinations" in tables
    expected_sensitive = {
        "normalized_key", "pix_key", "cpf", "document", "holder_name",
        "recipient_name", "raw_payload", "provider_payload_json",
    }
    for table in (PayoutDestinationVerificationAttempt.__table__, PayoutDestinationVerificationEvidence.__table__):
        assert not ({column.name.lower() for column in table.columns} & expected_sensitive)
    assert set(PayoutDestinationVerificationAttempt.__table__.columns.keys()) == {
        "id", "attempt_id", "idempotency_key", "destination_id", "destination_version",
        "key_type", "provider_name", "state", "requested_at", "requested_by", "created_at",
    }
    assert set(PayoutDestinationVerificationEvidence.__table__.columns.keys()) == {
        "id", "verification_attempt_id", "sequence", "result_state", "outcome",
        "reason_code", "provider_request_id", "provider_response_id", "provider_event_id",
        "provider_timestamp", "received_at", "authenticity_status", "authenticity_method",
        "authenticity_checked_at", "freshness", "evidence_digest", "created_at",
    }


def test_attempt_unique_keys_binding_and_one_active_attempt(database):
    with database.begin() as connection:
        _insert_attempt(connection)
        with pytest.raises(IntegrityError):
            _insert_attempt(connection, destination_id=81001, attempt_id="attempt-dup", idempotency_key="idem-dup")
    with database.begin() as connection:
        with pytest.raises(IntegrityError):
            _insert_attempt(connection, destination_id=81001, attempt_id="attempt-2", idempotency_key="idem-1")
        with pytest.raises(IntegrityError):
            _insert_attempt(connection, destination_id=81001, destination_version=2, attempt_id="bad-version", idempotency_key="bad-version-key")
        with pytest.raises(IntegrityError):
            _insert_attempt(connection, destination_id=81001, key_type="EMAIL", attempt_id="bad-type", idempotency_key="bad-type-key")


def test_attempt_id_is_globally_unique_and_new_attempt_allowed_after_final(database):
    destination_id = 81701
    _insert_destination(database, destination_id)
    with database.begin() as connection:
        _insert_attempt(connection, destination_id=destination_id, attempt_id="global-attempt-id", idempotency_key="global-idem-1")
        _insert_evidence(connection, _attempt_pk(connection, "global-attempt-id"), state="FINAL",
                         outcome="RETRYABLE", digest="sha256:" + "9" * 64)
        connection.execute(text("UPDATE payout_destination_verification_attempts SET state='FINAL' WHERE attempt_id='global-attempt-id'"))
        _insert_attempt(connection, destination_id=destination_id, attempt_id="after-final", idempotency_key="global-idem-2")

    other_destination_id = 81702
    _insert_destination(database, other_destination_id)
    with database.begin() as connection:
        with pytest.raises(IntegrityError):
            _insert_attempt(connection, destination_id=other_destination_id, attempt_id="global-attempt-id", idempotency_key="global-idem-3")


@pytest.mark.parametrize("transition", [("REQUESTED", "PENDING"), ("REQUESTED", "FINAL"), ("PENDING", "FINAL")])
def test_attempt_allowed_transitions_and_identity_immutability(database, transition):
    source, target = transition
    destination_id = 81100 + (0 if source == "REQUESTED" and target == "PENDING" else 1 if source == "REQUESTED" else 2)
    _insert_destination(database, destination_id)
    attempt_id = f"transition-{source}-{target}"
    with database.begin() as connection:
        _insert_attempt(connection, destination_id=destination_id, state="REQUESTED", attempt_id=attempt_id,
                        idempotency_key=f"idem-{attempt_id}")
        attempt_pk = _attempt_pk(connection, attempt_id)
        if source != "REQUESTED":
            _insert_evidence(connection, attempt_pk, state="PENDING")
            connection.execute(text("UPDATE payout_destination_verification_attempts SET state='PENDING' WHERE attempt_id=:attempt_id"),
                               {"attempt_id": attempt_id})
        _insert_evidence(connection, attempt_pk, sequence=2 if source != "REQUESTED" else 1,
                         state=target, outcome="CONFIRMED" if target == "FINAL" else None,
                         digest="sha256:" + "7" * 64 if target == "FINAL" else DIGEST)
        connection.execute(text("UPDATE payout_destination_verification_attempts SET state=:state WHERE attempt_id=:attempt_id"),
                           {"state": target, "attempt_id": attempt_id})
        # A state no-op remains valid without adding another result row.
        connection.execute(text("UPDATE payout_destination_verification_attempts SET state=:state WHERE attempt_id=:attempt_id"),
                           {"state": target, "attempt_id": attempt_id})
        with pytest.raises(IntegrityError):
            connection.execute(text("UPDATE payout_destination_verification_attempts SET destination_version=2 WHERE attempt_id=:attempt_id"),
                               {"attempt_id": attempt_id})
        with pytest.raises(IntegrityError):
            connection.execute(text("DELETE FROM payout_destination_verification_attempts WHERE attempt_id=:attempt_id"),
                               {"attempt_id": attempt_id})


@pytest.mark.parametrize("source,target", [("PENDING", "REQUESTED"), ("FINAL", "PENDING"), ("FINAL", "REQUESTED")])
def test_attempt_rejects_reverse_transitions(database, source, target):
    destination_id = 81200 + len(source) + len(target)
    _insert_destination(database, destination_id)
    attempt_id = f"reverse-{source}-{target}"
    with database.begin() as connection:
        _insert_attempt(connection, destination_id=destination_id, state="REQUESTED", attempt_id=attempt_id,
                        idempotency_key=f"idem-{attempt_id}")
        attempt_pk = _attempt_pk(connection, attempt_id)
        if source != "REQUESTED":
            _insert_evidence(connection, attempt_pk, state="PENDING")
            connection.execute(text("UPDATE payout_destination_verification_attempts SET state='PENDING' WHERE attempt_id=:attempt_id"),
                               {"attempt_id": attempt_id})
        if source == "FINAL":
            _insert_evidence(connection, attempt_pk, state="FINAL", outcome="NOT_CONFIRMED", sequence=2,
                             digest="sha256:" + "8" * 64)
            connection.execute(text("UPDATE payout_destination_verification_attempts SET state='FINAL' WHERE attempt_id=:attempt_id"),
                               {"attempt_id": attempt_id})
        with pytest.raises(IntegrityError):
            connection.execute(text("UPDATE payout_destination_verification_attempts SET state=:target WHERE attempt_id=:attempt_id"),
                               {"target": target, "attempt_id": attempt_id})


@pytest.mark.parametrize("status", ["REVOKED", "VERIFIED"])
def test_destination_non_unverified_cannot_start_attempt(database, status):
    destination_id = 81300 + (1 if status == "VERIFIED" else 0)
    if status == "REVOKED":
        _insert_destination(database, destination_id, status="REVOKED")
        with database.begin() as connection, pytest.raises(IntegrityError):
            _insert_attempt(connection, destination_id=destination_id, attempt_id=f"not-{status}", idempotency_key=f"not-{status}-key")
        return

    # Isolate a synthetic pre-existing VERIFIED row to exercise the 0102
    # binding guard; restore the 0101 trigger before testing the new guard.
    _insert_destination(database, destination_id)
    with database.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER trg_mpd_insert_guard")
        connection.exec_driver_sql("DROP TRIGGER trg_mpd_update_guard")
        connection.exec_driver_sql("DROP TRIGGER trg_mpd_delete_guard")
        connection.execute(text("""
            UPDATE member_payout_destinations
            SET verification_status='VERIFIED', verified_at='2026-01-02 03:04:00', verified_by=81001
            WHERE id=:id
        """), {"id": destination_id})
    from importlib.util import module_from_spec, spec_from_file_location
    migration_path = ROOT / "alembic/versions/0101_member_payout_destination_a377b4r2.py"
    spec = spec_from_file_location("a377b4r2_for_restore", migration_path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    with database.begin() as connection:
        migration._create_sqlite_guards(connection)
        with pytest.raises(IntegrityError):
            _insert_attempt(connection, destination_id=destination_id, attempt_id="not-verified", idempotency_key="not-verified-key")


def test_evidence_constraints_append_only_freshness_and_no_promotion(database):
    destination_id = 81401
    _insert_destination(database, destination_id)
    with database.begin() as connection:
        _insert_attempt(connection, destination_id=destination_id, attempt_id="evidence-attempt", idempotency_key="evidence-idem")
        attempt_pk = _attempt_pk(connection, "evidence-attempt")
        _insert_evidence(connection, attempt_pk)
        for freshness in ("STALE", "REVOKED", "NOT_UNVERIFIED"):
            _insert_evidence(connection, attempt_pk, sequence=2 + ("STALE", "REVOKED", "NOT_UNVERIFIED").index(freshness),
                             freshness=freshness, digest="sha256:" + str(2 + ("STALE", "REVOKED", "NOT_UNVERIFIED").index(freshness)) * 64)
        with pytest.raises(IntegrityError):
            _insert_evidence(connection, attempt_pk, sequence=1, digest="sha256:" + "b" * 64)
        with pytest.raises(IntegrityError):
            _insert_evidence(connection, attempt_pk, sequence=5, digest=DIGEST)
        with pytest.raises(IntegrityError):
            _insert_evidence(connection, attempt_pk, sequence=5, state="PENDING", outcome="CONFIRMED", digest="sha256:" + "c" * 64)
        with pytest.raises(IntegrityError):
            _insert_evidence(connection, attempt_pk, sequence=5, state="FINAL", outcome=None, digest="sha256:" + "d" * 64)
        _insert_evidence(connection, attempt_pk, sequence=5, state="FINAL", outcome="CONFIRMED",
                         digest="sha256:" + "e" * 64, authenticity="AUTHENTIC",
                         method="authenticated-response", checked_at="2026-01-02 03:04:00+00:00")
        with pytest.raises(IntegrityError):
            _insert_evidence(connection, attempt_pk, sequence=6, state="FINAL", outcome="CONFIRMED", digest="sha256:" + "f" * 64)
        with pytest.raises(IntegrityError):
            _insert_evidence(connection, attempt_pk, sequence=6, state="PENDING", digest="sha256:" + "7" * 64)
        with pytest.raises(IntegrityError):
            connection.execute(text("UPDATE payout_destination_verification_evidence SET reason_code='changed' WHERE verification_attempt_id=:id"), {"id": attempt_pk})
        with pytest.raises(IntegrityError):
            connection.execute(text("DELETE FROM payout_destination_verification_evidence WHERE verification_attempt_id=:id"), {"id": attempt_pk})
        status = connection.execute(text("SELECT verification_status FROM member_payout_destinations WHERE id=:id"), {"id": destination_id}).scalar_one()
        assert status == "UNVERIFIED"


def test_authenticity_constraints_and_provider_ids_are_nullable_nonunique(database):
    destination_id = 81501
    _insert_destination(database, destination_id)
    with database.begin() as connection:
        _insert_attempt(connection, destination_id=destination_id, attempt_id="auth-attempt", idempotency_key="auth-idem")
        pk = _attempt_pk(connection, "auth-attempt")
        _insert_evidence(connection, pk, digest="sha256:" + "1" * 64)
        with pytest.raises(IntegrityError):
            _insert_evidence(connection, pk, sequence=2, digest="sha256:" + "2" * 64,
                             authenticity="AUTHENTIC", checked_at=None)
        _insert_evidence(connection, pk, sequence=2, state="FINAL", outcome="NOT_CONFIRMED",
                         digest="sha256:" + "3" * 64, authenticity="AUTHENTIC",
                         method="authenticated-response", checked_at="2026-01-02 03:04:00+00:00",
                         provider_event_id="reused-event")
        columns = {column["name"]: column for column in inspect(connection).get_columns("payout_destination_verification_evidence")}
        for name in ("provider_request_id", "provider_response_id", "provider_event_id"):
            assert columns[name]["nullable"] is True

    other_destination_id = 81502
    _insert_destination(database, other_destination_id)
    with database.begin() as connection:
        _insert_attempt(connection, destination_id=other_destination_id, attempt_id="auth-attempt-2", idempotency_key="auth-idem-2")
        _insert_evidence(connection, _attempt_pk(connection, "auth-attempt-2"), provider_event_id="reused-event")


@pytest.mark.parametrize("target", ["PENDING", "FINAL"])
def test_attempt_transition_requires_matching_evidence(database, target):
    destination_id = 81800 + (1 if target == "FINAL" else 0)
    _insert_destination(database, destination_id)
    attempt_id = f"requires-{target}"
    with database.begin() as connection:
        _insert_attempt(connection, destination_id=destination_id, attempt_id=attempt_id,
                        idempotency_key=f"requires-idem-{target}")
        if target == "FINAL":
            pending_digest = "sha256:" + "4" * 64
            _insert_evidence(connection, _attempt_pk(connection, attempt_id), digest=pending_digest)
            connection.execute(text("UPDATE payout_destination_verification_attempts SET state='PENDING' WHERE attempt_id=:id"),
                               {"id": attempt_id})
        with pytest.raises(IntegrityError):
            connection.execute(text("UPDATE payout_destination_verification_attempts SET state=:state WHERE attempt_id=:id"),
                               {"state": target, "id": attempt_id})

        attempt_pk = _attempt_pk(connection, attempt_id)
        outcome = "CONFIRMED" if target == "FINAL" else None
        _insert_evidence(connection, attempt_pk, sequence=2 if target == "FINAL" else 1,
                         state=target, outcome=outcome, digest="sha256:" + ("5" if target == "FINAL" else "6") * 64)
        connection.execute(text("UPDATE payout_destination_verification_attempts SET state=:state WHERE attempt_id=:id"),
                           {"state": target, "id": attempt_id})


@pytest.mark.parametrize("initial_state", ["REQUESTED", "PENDING"])
def test_final_evidence_is_terminal_before_attempt_state_changes(database, initial_state):
    destination_id = 81900 + (1 if initial_state == "PENDING" else 0)
    _insert_destination(database, destination_id)
    attempt_id = f"terminal-evidence-{initial_state}"
    with database.begin() as connection:
        _insert_attempt(connection, destination_id=destination_id, attempt_id=attempt_id,
                        idempotency_key=f"terminal-idem-{initial_state}")
        attempt_pk = _attempt_pk(connection, attempt_id)
        if initial_state == "PENDING":
            _insert_evidence(connection, attempt_pk, digest="sha256:" + "1" * 64)
            connection.execute(text("UPDATE payout_destination_verification_attempts SET state='PENDING' WHERE attempt_id=:id"),
                               {"id": attempt_id})
        _insert_evidence(connection, attempt_pk, sequence=2 if initial_state == "PENDING" else 1,
                         state="FINAL", outcome="CONFIRMED", digest="sha256:" + "2" * 64)
        with pytest.raises(IntegrityError):
            _insert_evidence(connection, attempt_pk, sequence=3, digest="sha256:" + "3" * 64)
        with pytest.raises(IntegrityError):
            _insert_evidence(connection, attempt_pk, sequence=3, state="FINAL", outcome="CONFIRMED",
                             digest="sha256:" + "4" * 64)


@pytest.mark.parametrize("evidence_state", ["PENDING", "FINAL"])
def test_attempt_final_rejects_all_later_evidence(database, evidence_state):
    destination_id = 82000 + (1 if evidence_state == "FINAL" else 0)
    _insert_destination(database, destination_id)
    attempt_id = f"closed-attempt-{evidence_state}"
    with database.begin() as connection:
        _insert_attempt(connection, destination_id=destination_id, attempt_id=attempt_id,
                        idempotency_key=f"closed-idem-{evidence_state}")
        attempt_pk = _attempt_pk(connection, attempt_id)
        if evidence_state == "PENDING":
            _insert_evidence(connection, attempt_pk, state="PENDING")
            connection.execute(text("UPDATE payout_destination_verification_attempts SET state='PENDING' WHERE attempt_id=:id"),
                               {"id": attempt_id})
        _insert_evidence(connection, attempt_pk, sequence=2 if evidence_state == "PENDING" else 1,
                         state="FINAL", outcome="CONFIRMED", digest="sha256:" + "9" * 64)
        connection.execute(text("UPDATE payout_destination_verification_attempts SET state='FINAL' WHERE attempt_id=:id"),
                           {"id": attempt_id})
        with pytest.raises(IntegrityError):
            _insert_evidence(connection, attempt_pk, sequence=3, state="PENDING",
                             digest="sha256:" + "a" * 64)
        with pytest.raises(IntegrityError):
            _insert_evidence(connection, attempt_pk, sequence=3, state="FINAL", outcome="CONFIRMED",
                             digest="sha256:" + "b" * 64)


def test_0101_still_blocks_verified_transition_and_insert(database):
    destination_id = 81601
    _insert_destination(database, destination_id)
    with database.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(text("""
                UPDATE member_payout_destinations
                SET verification_status='VERIFIED', verified_at='2026-01-02 03:04:00', verified_by=81001
                WHERE id=:id
            """), {"id": destination_id})
        with pytest.raises(IntegrityError):
            connection.execute(text("""
                INSERT INTO member_payout_destinations
                    (member_id, version, key_type, encrypted_value, masked_value, verification_status,
                     created_at, created_by, verified_at, verified_by)
                VALUES (81001, 99, 'CPF', 'fixture', '***', 'VERIFIED',
                        '2026-01-02 03:04:00', 81001, '2026-01-02 03:04:00', 81001)
            """))


def test_attempt_requested_by_and_model_timestamp_validation(database):
    attempt = PayoutDestinationVerificationAttempt(
        attempt_id="model-attempt", idempotency_key="model-idem", destination_id=81001,
        destination_version=1, key_type="CPF", provider_name="provider-neutral",
        requested_at=NOW, requested_by=None,
    )
    assert attempt.requested_by is None
    with pytest.raises(ValueError):
        PayoutDestinationVerificationAttempt(
            attempt_id="naive", idempotency_key="naive-idem", destination_id=81001,
            destination_version=1, key_type="CPF", provider_name="provider-neutral",
            requested_at=datetime(2026, 1, 2), requested_by=None,
        )


def test_sqlite_indexes_and_foreign_keys(database):
    attempt_indexes = {idx["name"]: idx for idx in inspect(database).get_indexes("payout_destination_verification_attempts")}
    evidence_indexes = {idx["name"]: idx for idx in inspect(database).get_indexes("payout_destination_verification_evidence")}
    assert ACTIVE_INDEX in attempt_indexes
    assert FINAL_INDEX in evidence_indexes
    with database.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(text("""
                INSERT INTO payout_destination_verification_attempts
                    (attempt_id,idempotency_key,destination_id,destination_version,key_type,provider_name,state,requested_at,created_at)
                VALUES ('bad-fk','bad-fk-key',999999,1,'CPF','provider','REQUESTED','2026-01-02','2026-01-02')
            """))


def test_postgresql_guard_contract_is_present_without_live_connection():
    source = (ROOT / "alembic/versions/0102_payout_verification_evidence_a377b4r3.py").read_text()
    for text_fragment in (
        "CREATE FUNCTION pdva_guard()", "CREATE TRIGGER trg_pdva_guard",
        "CREATE FUNCTION pdve_guard()", "CREATE TRIGGER trg_pdve_guard",
        "DROP TRIGGER IF EXISTS trg_pdva_guard", "DROP TRIGGER IF EXISTS trg_pdve_guard",
        "DROP FUNCTION IF EXISTS pdva_guard()", "DROP FUNCTION IF EXISTS pdve_guard()",
        "postgresql_where=sa.text(\"state IN ('REQUESTED', 'PENDING')\")",
        "postgresql_where=sa.text(\"result_state = 'FINAL'\")",
        "RAISE EXCEPTION 'PENDING state requires pending evidence'",
        "RAISE EXCEPTION 'FINAL state requires final evidence'",
        "attempt_state NOT IN ('REQUESTED', 'PENDING')",
        "verification evidence cannot follow final evidence",
        "FOR UPDATE;",
    ):
        assert text_fragment in source


def test_real_0102_downgrade_returns_to_0101(tmp_path):
    database = tmp_path / "payout-verification-downgrade.sqlite"
    _alembic(database, "upgrade", REVISION)
    _alembic(database, "downgrade", PREVIOUS)
    engine = sa.create_engine(f"sqlite:///{database}")
    tables = set(inspect(engine).get_table_names())
    assert "payout_destination_verification_attempts" not in tables
    assert "payout_destination_verification_evidence" not in tables
    assert "member_payout_destinations" in tables
    engine.dispose()


def test_migration_chain_and_no_sensitive_columns():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_current_head() == REVISION
    assert scripts.get_revision(REVISION).down_revision == PREVIOUS
