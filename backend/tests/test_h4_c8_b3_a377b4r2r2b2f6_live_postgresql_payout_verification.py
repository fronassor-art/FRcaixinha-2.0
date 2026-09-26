"""Disposable PostgreSQL integration validation for payout verification.

The test refuses PostgreSQL URLs outside the dedicated local CI database.
All identities and payout destinations are synthetic and are removed by the
fixture after each test. SQLite runs skip this module's cases explicitly.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from itertools import count
from threading import Barrier

import pytest
import sqlalchemy as sa
from sqlalchemy import event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.payout_destination_verification import (
    VerificationOutcome,
    VerificationResult,
    VerificationResultState,
)
from app.models import (
    Group,
    Member,
    MemberPayoutDestination,
    PayoutDestinationVerificationAttempt,
    PayoutDestinationVerificationEvidence,
    User,
)
from app.services import payout_destination_verification as verification_service


ALEMBIC_HEAD = "0102_payout_verification_evidence_a377b4r3"
_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
_IDS = count(7_100_000)


@dataclass(frozen=True)
class SyntheticIdentity:
    group_id: int
    user_id: int
    member_id: int
    destination_id: int


@pytest.fixture(scope="module")
def pg_engine():
    raw_url = os.environ.get("DATABASE_URL")
    if not raw_url:
        pytest.skip("DATABASE_URL is not configured for live PostgreSQL tests")
    parsed = make_url(raw_url)
    if parsed.get_backend_name() != "postgresql":
        pytest.skip("live PostgreSQL tests skipped for a non-PostgreSQL dialect")
    if (
        parsed.database != "frcaixinha_test"
        or parsed.username != "frcaixinha_test"
        or parsed.host != "127.0.0.1"
    ):
        pytest.fail("live PostgreSQL tests require the disposable CI database")

    engine = sa.create_engine(parsed, pool_pre_ping=True, pool_size=6, max_overflow=4)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        pytest.fail("DATABASE_URL did not create a PostgreSQL engine")
    with engine.connect() as connection:
        revisions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    assert revisions == [ALEMBIC_HEAD]
    yield engine
    engine.dispose()


@pytest.fixture()
def synthetic_identity_factory(pg_engine):
    created: list[SyntheticIdentity] = []

    def create() -> SyntheticIdentity:
        serial = next(_IDS)
        identity = SyntheticIdentity(
            group_id=serial,
            user_id=serial,
            member_id=serial,
            destination_id=serial,
        )
        with Session(pg_engine) as session:
            session.add(Group(id=identity.group_id, name=f"PG verification fixture {serial}"))
            session.add(
                User(
                    id=identity.user_id,
                    name=f"Synthetic PG member {serial}",
                    email=f"pg-verification-{serial}@example.test",
                    cpf=f"PG{serial:09d}",
                    password_hash="test-only-not-a-credential",
                )
            )
            session.flush()
            session.add(
                Member(
                    id=identity.member_id,
                    user_id=identity.user_id,
                    group_id=identity.group_id,
                )
            )
            session.add(
                MemberPayoutDestination(
                    id=identity.destination_id,
                    member_id=identity.member_id,
                    version=1,
                    key_type="CPF",
                    encrypted_value="synthetic-ciphertext-never-read",
                    masked_value="***0000",
                    verification_status="UNVERIFIED",
                    created_at=_NOW,
                    created_by=identity.user_id,
                )
            )
            session.commit()
        created.append(identity)
        return identity

    yield create

    # The database is a disposable CI service. Disable only row lifecycle
    # triggers while removing this fixture's own rows; constraints remain on.
    with pg_engine.begin() as connection:
        for table in (
            "payout_destination_verification_evidence",
            "payout_destination_verification_attempts",
            "member_payout_destinations",
        ):
            connection.exec_driver_sql(f"ALTER TABLE {table} DISABLE TRIGGER USER")
        for identity in created:
            connection.execute(
                text(
                    "DELETE FROM payout_destination_verification_evidence "
                    "WHERE verification_attempt_id IN (SELECT id "
                    "FROM payout_destination_verification_attempts WHERE destination_id IN "
                    "(SELECT id FROM member_payout_destinations WHERE member_id=:member))"
                ),
                {"member": identity.member_id},
            )
            connection.execute(
                text(
                    "DELETE FROM payout_destination_verification_attempts WHERE destination_id IN "
                    "(SELECT id FROM member_payout_destinations WHERE member_id=:member)"
                ),
                {"member": identity.member_id},
            )
            connection.execute(
                text("DELETE FROM member_payout_destinations WHERE member_id=:member"),
                {"member": identity.member_id},
            )
            connection.execute(text("DELETE FROM members WHERE id=:id"), {"id": identity.member_id})
            connection.execute(text("DELETE FROM users WHERE id=:id"), {"id": identity.user_id})
            connection.execute(text("DELETE FROM groups WHERE id=:id"), {"id": identity.group_id})
        for table in (
            "member_payout_destinations",
            "payout_destination_verification_attempts",
            "payout_destination_verification_evidence",
        ):
            connection.exec_driver_sql(f"ALTER TABLE {table} ENABLE TRIGGER USER")


def _start(session: Session, identity: SyntheticIdentity, *, idempotency_key: str):
    return verification_service.start_verification_attempt(
        session,
        destination_id=identity.destination_id,
        idempotency_key=idempotency_key,
        provider_name="synthetic-ci-provider",
        requested_by=identity.user_id,
        requested_at=_NOW,
    )


def _final_result(attempt) -> VerificationResult:
    return VerificationResult(
        attempt_id=attempt.attempt_id,
        state=VerificationResultState.FINAL,
        outcome=VerificationOutcome.CONFIRMED,
        provider_name=attempt.provider_name,
        provider_request_id="ci-request-1",
        provider_response_id="ci-response-1",
        provider_event_id="ci-event-1",
        provider_timestamp=_NOW,
        received_at=_NOW,
        reason_code="ownership.confirmed",
    )


def _record_final(
    session: Session,
    result: VerificationResult,
    *,
    authenticity_checked_at: datetime = _NOW,
):
    return verification_service.record_verification_result(
        session,
        result=result,
        authenticity_status="AUTHENTIC",
        authenticity_method="synthetic-ci-authenticated-response",
        authenticity_checked_at=authenticity_checked_at,
    )


def _attempt_values(identity: SyntheticIdentity, suffix: str, **overrides):
    values = {
        "attempt_id": f"pg-attempt-{identity.destination_id}-{suffix}",
        "idempotency_key": f"pg-idem-{identity.destination_id}-{suffix}",
        "destination_id": identity.destination_id,
        "destination_version": 1,
        "key_type": "CPF",
        "provider_name": "synthetic-ci-provider",
        "state": "REQUESTED",
        "requested_at": _NOW,
        "requested_by": identity.user_id,
        "created_at": _NOW,
        "now": _NOW,
        "actor": identity.user_id,
    }
    values.update(overrides)
    return values


def _insert_attempt(session: Session, identity: SyntheticIdentity, suffix: str, **overrides) -> int:
    row_id = session.execute(
        text(
            "INSERT INTO payout_destination_verification_attempts "
            "(attempt_id,idempotency_key,destination_id,destination_version,key_type,provider_name,state,requested_at,requested_by,created_at) "
            "VALUES (:attempt_id,:idempotency_key,:destination_id,:destination_version,:key_type,:provider_name,:state,:requested_at,:requested_by,:created_at) "
            "RETURNING id"
        ),
        _attempt_values(identity, suffix, **overrides),
    ).scalar_one()
    return int(row_id)


def _insert_evidence(
    session: Session,
    attempt_id: int,
    *,
    sequence: int,
    state: str,
    digest_digit: str,
    outcome: str | None = None,
) -> int:
    row_id = session.execute(
        text(
            "INSERT INTO payout_destination_verification_evidence "
            "(verification_attempt_id,sequence,result_state,outcome,received_at,authenticity_status,freshness,evidence_digest,created_at) "
            "VALUES (:attempt_id,:sequence,:state,:outcome,:received_at,'NOT_CHECKED','CURRENT',:digest,:created_at) "
            "RETURNING id"
        ),
        {
            "attempt_id": attempt_id,
            "sequence": sequence,
            "state": state,
            "outcome": outcome,
            "received_at": _NOW,
            "digest": "sha256:" + digest_digit * 64,
            "created_at": _NOW,
        },
    ).scalar_one()
    return int(row_id)


def _must_reject(session: Session, statement: str, parameters: dict | None = None) -> None:
    with pytest.raises(SQLAlchemyError):
        with session.begin_nested():
            session.execute(text(statement), parameters or {})


def test_alembic_head_and_0101_destination_lifecycle_guards_live(
    pg_engine, synthetic_identity_factory
):
    identity = synthetic_identity_factory()
    with Session(pg_engine) as session:
        assert session.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == ALEMBIC_HEAD
        assert session.execute(
            text("SELECT verification_status FROM member_payout_destinations WHERE id=:id"),
            {"id": identity.destination_id},
        ).scalar_one() == "UNVERIFIED"

        _must_reject(
            session,
            "INSERT INTO member_payout_destinations "
            "(id,member_id,version,key_type,encrypted_value,masked_value,verification_status,created_at,created_by,verified_at,verified_by,revoked_at,revoked_by) "
            "VALUES (:id,:member,2,'CPF','synthetic','***','VERIFIED',:now,:actor,:now,:actor,NULL,NULL)",
            {"id": identity.destination_id + 50_000_000, "member": identity.member_id, "now": _NOW, "actor": identity.user_id},
        )
        _must_reject(
            session,
            "UPDATE member_payout_destinations SET verification_status='VERIFIED',verified_at=:now,verified_by=:actor WHERE id=:id",
            {"now": _NOW, "actor": identity.user_id, "id": identity.destination_id},
        )

        session.execute(
            text("UPDATE member_payout_destinations SET verification_status='REVOKED',revoked_at=:now,revoked_by=:actor WHERE id=:id"),
            {"now": _NOW, "actor": identity.user_id, "id": identity.destination_id},
        )
        _must_reject(
            session,
            "UPDATE member_payout_destinations SET verification_status='VERIFIED',verified_at=:now,verified_by=:actor WHERE id=:id",
            {"now": _NOW, "actor": identity.user_id, "id": identity.destination_id},
        )
        assert session.execute(
            text("SELECT verification_status FROM member_payout_destinations WHERE id=:id"),
            {"id": identity.destination_id},
        ).scalar_one() == "REVOKED"


def test_0102_binding_active_index_and_closed_destination_attempt_guards_live(
    pg_engine, synthetic_identity_factory
):
    identity = synthetic_identity_factory()
    with Session(pg_engine) as session:
        active_id = _insert_attempt(session, identity, "valid")
        _must_reject(
            session,
            "INSERT INTO payout_destination_verification_attempts "
            "(attempt_id,idempotency_key,destination_id,destination_version,key_type,provider_name,state,requested_at,requested_by,created_at) "
            "VALUES (:attempt_id,:idempotency_key,:destination_id,:destination_version,:key_type,:provider_name,'REQUESTED',:now,:actor,:now)",
            _attempt_values(identity, "duplicate-active"),
        )
        _must_reject(
            session,
            "INSERT INTO payout_destination_verification_attempts "
            "(attempt_id,idempotency_key,destination_id,destination_version,key_type,provider_name,state,requested_at,requested_by,created_at) "
            "VALUES (:attempt_id,:idempotency_key,:destination_id,:destination_version,:key_type,:provider_name,'REQUESTED',:now,:actor,:now)",
            _attempt_values(identity, "wrong-version", destination_version=2),
        )
        _must_reject(
            session,
            "INSERT INTO payout_destination_verification_attempts "
            "(attempt_id,idempotency_key,destination_id,destination_version,key_type,provider_name,state,requested_at,requested_by,created_at) "
            "VALUES (:attempt_id,:idempotency_key,:destination_id,:destination_version,:key_type,:provider_name,'REQUESTED',:now,:actor,:now)",
            _attempt_values(identity, "wrong-key-type", key_type="PHONE"),
        )
        assert active_id > 0
        session.rollback()

    # REVOKED is a normal, guarded transition; neither service nor direct
    # database insertion may start another attempt against that row.
    with Session(pg_engine) as session:
        session.execute(
            text("UPDATE member_payout_destinations SET verification_status='REVOKED',revoked_at=:now,revoked_by=:actor WHERE id=:id"),
            {"now": _NOW, "actor": identity.user_id, "id": identity.destination_id},
        )
        session.commit()
        with pytest.raises(verification_service.PayoutVerificationConflict):
            _start(session, identity, idempotency_key=f"service-revoked-{identity.destination_id}")
        session.rollback()
        _must_reject(
            session,
            "INSERT INTO payout_destination_verification_attempts "
            "(attempt_id,idempotency_key,destination_id,destination_version,key_type,provider_name,state,requested_at,requested_by,created_at) "
            "VALUES (:attempt_id,:idempotency_key,:destination_id,:destination_version,:key_type,:provider_name,'REQUESTED',:now,:actor,:now)",
            _attempt_values(identity, "revoked"),
        )


def test_0101_controls_insert_and_update_to_verified_live(pg_engine, synthetic_identity_factory):
    identity = synthetic_identity_factory()
    with Session(pg_engine) as session:
        session.execute(text("ALTER TABLE member_payout_destinations DISABLE TRIGGER trg_mpd_guard"))
        session.execute(
            text("UPDATE member_payout_destinations SET verification_status='VERIFIED',verified_at=:now,verified_by=:actor WHERE id=:id"),
            {"now": _NOW, "actor": identity.user_id, "id": identity.destination_id},
        )
        session.execute(text("ALTER TABLE member_payout_destinations ENABLE TRIGGER trg_mpd_guard"))
        session.commit()

    with Session(pg_engine) as session:
        with pytest.raises(verification_service.PayoutVerificationConflict):
            _start(session, identity, idempotency_key=f"service-verified-{identity.destination_id}")
        session.rollback()
        _must_reject(
            session,
            "INSERT INTO payout_destination_verification_attempts "
            "(attempt_id,idempotency_key,destination_id,destination_version,key_type,provider_name,state,requested_at,requested_by,created_at) "
            "VALUES (:attempt_id,:idempotency_key,:destination_id,:destination_version,:key_type,:provider_name,'REQUESTED',:now,:actor,:now)",
            _attempt_values(identity, "verified"),
        )


def test_attempt_evidence_lifecycle_append_only_and_partial_indexes_live(
    pg_engine, synthetic_identity_factory
):
    identity = synthetic_identity_factory()
    with Session(pg_engine, expire_on_commit=False) as session:
        attempt = _start(session, identity, idempotency_key=f"lifecycle-{identity.destination_id}")
        session.commit()
        attempt_id = attempt.id

    with Session(pg_engine) as session:
        _must_reject(
            session,
            "UPDATE payout_destination_verification_attempts SET state='PENDING' WHERE id=:id",
            {"id": attempt_id},
        )
        _must_reject(
            session,
            "UPDATE payout_destination_verification_attempts SET state='FINAL' WHERE id=:id",
            {"id": attempt_id},
        )
        pending_evidence_id = _insert_evidence(
            session, attempt_id, sequence=1, state="PENDING", digest_digit="0"
        )
        session.execute(
            text("UPDATE payout_destination_verification_attempts SET state='PENDING' WHERE id=:id"),
            {"id": attempt_id},
        )
        _must_reject(
            session,
            "INSERT INTO payout_destination_verification_evidence "
            "(verification_attempt_id,sequence,result_state,received_at,authenticity_status,freshness,evidence_digest,created_at) "
            "VALUES (:attempt,2,'PENDING',:now,'NOT_CHECKED','CURRENT',:digest,:now)",
            {"attempt": attempt_id, "now": _NOW, "digest": "sha256:" + "0" * 64},
        )
        _must_reject(
            session,
            "INSERT INTO payout_destination_verification_evidence "
            "(verification_attempt_id,sequence,result_state,received_at,authenticity_status,freshness,evidence_digest,created_at) "
            "VALUES (:attempt,1,'PENDING',:now,'NOT_CHECKED','CURRENT',:digest,:now)",
            {"attempt": attempt_id, "now": _NOW, "digest": "sha256:" + "1" * 64},
        )
        _must_reject(
            session,
            "UPDATE payout_destination_verification_attempts SET state='FINAL' WHERE id=:id",
            {"id": attempt_id},
        )
        final_evidence_id = _insert_evidence(
            session,
            attempt_id,
            sequence=2,
            state="FINAL",
            digest_digit="2",
            outcome="CONFIRMED",
        )
        _must_reject(
            session,
            "INSERT INTO payout_destination_verification_evidence "
            "(verification_attempt_id,sequence,result_state,outcome,received_at,authenticity_status,freshness,evidence_digest,created_at) "
            "VALUES (:attempt,3,'PENDING',NULL,:now,'NOT_CHECKED','CURRENT',:digest,:now)",
            {"attempt": attempt_id, "now": _NOW, "digest": "sha256:" + "3" * 64},
        )
        _must_reject(
            session,
            "INSERT INTO payout_destination_verification_evidence "
            "(verification_attempt_id,sequence,result_state,outcome,received_at,authenticity_status,freshness,evidence_digest,created_at) "
            "VALUES (:attempt,4,'FINAL','CONFIRMED',:now,'NOT_CHECKED','CURRENT',:digest,:now)",
            {"attempt": attempt_id, "now": _NOW, "digest": "sha256:" + "4" * 64},
        )
        session.execute(
            text("UPDATE payout_destination_verification_attempts SET state='FINAL' WHERE id=:id"),
            {"id": attempt_id},
        )
        # Bypass only the insert trigger in this disposable fixture so the
        # partial unique FINAL index itself is exercised by PostgreSQL.
        session.execute(
            text("ALTER TABLE payout_destination_verification_evidence DISABLE TRIGGER USER")
        )
        _must_reject(
            session,
            "INSERT INTO payout_destination_verification_evidence "
            "(verification_attempt_id,sequence,result_state,outcome,received_at,authenticity_status,freshness,evidence_digest,created_at) "
            "VALUES (:attempt,3,'FINAL','CONFIRMED',:now,'NOT_CHECKED','CURRENT',:digest,:now)",
            {"attempt": attempt_id, "now": _NOW, "digest": "sha256:" + "5" * 64},
        )
        session.execute(
            text("ALTER TABLE payout_destination_verification_evidence ENABLE TRIGGER USER")
        )
        _must_reject(
            session,
            "UPDATE payout_destination_verification_attempts SET idempotency_key='tampered' WHERE id=:id",
            {"id": attempt_id},
        )
        _must_reject(
            session,
            "DELETE FROM payout_destination_verification_attempts WHERE id=:id",
            {"id": attempt_id},
        )
        _must_reject(
            session,
            "UPDATE payout_destination_verification_evidence SET reason_code='tampered' WHERE id=:id",
            {"id": pending_evidence_id},
        )
        _must_reject(
            session,
            "DELETE FROM payout_destination_verification_evidence WHERE id=:id",
            {"id": final_evidence_id},
        )
        session.commit()

    with Session(pg_engine) as session:
        assert session.execute(
            text("SELECT state FROM payout_destination_verification_attempts WHERE id=:id"),
            {"id": attempt_id},
        ).scalar_one() == "FINAL"
        assert session.execute(
            text("SELECT count(*) FROM payout_destination_verification_evidence WHERE verification_attempt_id=:id"),
            {"id": attempt_id},
        ).scalar_one() == 2
        assert session.execute(
            text("SELECT verification_status FROM member_payout_destinations WHERE id=:id"),
            {"id": identity.destination_id},
        ).scalar_one() == "UNVERIFIED"


def test_service_final_confirmed_current_leaves_destination_unverified_live(
    pg_engine, synthetic_identity_factory
):
    identity = synthetic_identity_factory()
    with Session(pg_engine, expire_on_commit=False) as session:
        attempt = _start(session, identity, idempotency_key=f"current-{identity.destination_id}")
        session.commit()
        result = _final_result(attempt)
        evidence = _record_final(session, result)
        assert evidence.freshness == "CURRENT"
        session.commit()
        assert session.execute(
            select(MemberPayoutDestination.verification_status).where(
                MemberPayoutDestination.id == identity.destination_id
            )
        ).scalar_one() == "UNVERIFIED"


def test_same_idempotency_start_serializes_two_postgresql_sessions_live(
    pg_engine, synthetic_identity_factory
):
    identity = synthetic_identity_factory()
    barrier = Barrier(2)
    lock_statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        if "for update" in statement.lower():
            lock_statements.append(statement.lower())

    event.listen(pg_engine, "before_cursor_execute", capture)

    def worker():
        with Session(pg_engine, expire_on_commit=False) as session:
            barrier.wait(timeout=20)
            attempt = _start(
                session, identity, idempotency_key=f"same-key-{identity.destination_id}"
            )
            result = attempt.attempt_id
            session.commit()
            return result

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempt_ids = list(pool.map(lambda _index: worker(), range(2)))
    finally:
        event.remove(pg_engine, "before_cursor_execute", capture)

    assert attempt_ids[0] == attempt_ids[1]
    assert any("members" in statement for statement in lock_statements)
    with Session(pg_engine) as session:
        assert session.execute(
            text("SELECT count(*) FROM payout_destination_verification_attempts WHERE destination_id=:id"),
            {"id": identity.destination_id},
        ).scalar_one() == 1


def test_different_active_attempts_serialize_to_one_and_conflict_live(
    pg_engine, synthetic_identity_factory
):
    identity = synthetic_identity_factory()
    barrier = Barrier(2)

    def worker(key: str):
        with Session(pg_engine, expire_on_commit=False) as session:
            barrier.wait(timeout=20)
            try:
                attempt = _start(session, identity, idempotency_key=key)
                attempt_id = attempt.attempt_id
                session.commit()
                return ("created", attempt_id)
            except verification_service.PayoutVerificationConflict:
                session.rollback()
                return ("conflict", None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(worker, (f"active-a-{identity.destination_id}", f"active-b-{identity.destination_id}")))
    assert sorted(kind for kind, _attempt_id in outcomes) == ["conflict", "created"]
    with Session(pg_engine) as session:
        assert session.execute(
            text("SELECT count(*) FROM payout_destination_verification_attempts WHERE destination_id=:id AND state IN ('REQUESTED','PENDING')"),
            {"id": identity.destination_id},
        ).scalar_one() == 1


def test_same_final_result_concurrently_replays_under_postgresql_locks_live(
    pg_engine, synthetic_identity_factory
):
    identity = synthetic_identity_factory()
    with Session(pg_engine, expire_on_commit=False) as session:
        attempt = _start(session, identity, idempotency_key=f"result-race-{identity.destination_id}")
        session.commit()
        result = _final_result(attempt)

    barrier = Barrier(2)
    lock_statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        if "for update" in statement.lower():
            lock_statements.append(statement.lower())

    event.listen(pg_engine, "before_cursor_execute", capture)

    def worker(worker_index: int):
        with Session(pg_engine) as session:
            barrier.wait(timeout=20)
            evidence = _record_final(
                session,
                replace(result, received_at=_NOW + timedelta(seconds=worker_index)),
                authenticity_checked_at=_NOW + timedelta(seconds=worker_index),
            )
            evidence_id = evidence.id
            session.commit()
            return evidence_id, worker_index

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            evidence_ids = list(pool.map(worker, (1, 2)))
    finally:
        event.remove(pg_engine, "before_cursor_execute", capture)

    assert evidence_ids[0][0] == evidence_ids[1][0]
    assert any("members" in statement for statement in lock_statements)
    assert any("payout_destination_verification_attempts" in statement for statement in lock_statements)
    with Session(pg_engine) as session:
        assert session.execute(
            text("SELECT count(*) FROM payout_destination_verification_evidence WHERE verification_attempt_id=(SELECT id FROM payout_destination_verification_attempts WHERE attempt_id=:attempt)"),
            {"attempt": result.attempt_id},
        ).scalar_one() == 1
        stored_received_at = session.execute(
            text("SELECT received_at FROM payout_destination_verification_evidence WHERE verification_attempt_id=(SELECT id FROM payout_destination_verification_attempts WHERE attempt_id=:attempt)"),
            {"attempt": result.attempt_id},
        ).scalar_one()
        assert stored_received_at in {_NOW + timedelta(seconds=1), _NOW + timedelta(seconds=2)}
        assert session.execute(
            text("SELECT state FROM payout_destination_verification_attempts WHERE attempt_id=:attempt"),
            {"attempt": result.attempt_id},
        ).scalar_one() == "FINAL"
        assert session.execute(
            text("SELECT verification_status FROM member_payout_destinations WHERE id=:destination"),
            {"destination": identity.destination_id},
        ).scalar_one() == "UNVERIFIED"


@pytest.mark.parametrize(("replacement", "expected_freshness"), [(False, "REVOKED"), (True, "STALE")])
def test_post_attempt_revoke_or_replacement_freshness_live(
    pg_engine, synthetic_identity_factory, replacement, expected_freshness
):
    identity = synthetic_identity_factory()
    with Session(pg_engine, expire_on_commit=False) as session:
        attempt = _start(session, identity, idempotency_key=f"freshness-{identity.destination_id}")
        session.commit()
        result = _final_result(attempt)

    with Session(pg_engine) as session:
        session.execute(
            text("UPDATE member_payout_destinations SET verification_status='REVOKED',revoked_at=:now,revoked_by=:actor WHERE id=:id"),
            {"now": _NOW, "actor": identity.user_id, "id": identity.destination_id},
        )
        if replacement:
            session.add(
                MemberPayoutDestination(
                    id=identity.destination_id + 60_000_000,
                    member_id=identity.member_id,
                    version=2,
                    key_type="CPF",
                    encrypted_value="synthetic-replacement-ciphertext",
                    masked_value="***0001",
                    verification_status="UNVERIFIED",
                    created_at=_NOW,
                    created_by=identity.user_id,
                )
            )
        session.commit()

    with Session(pg_engine) as session:
        evidence = _record_final(session, result)
        assert evidence.freshness == expected_freshness
        session.commit()
        assert session.execute(
            text("SELECT verification_status FROM member_payout_destinations WHERE id=:id"),
            {"id": identity.destination_id},
        ).scalar_one() == "REVOKED"
        if replacement:
            assert session.execute(
                text("SELECT verification_status FROM member_payout_destinations WHERE id=:id"),
                {"id": identity.destination_id + 60_000_000},
            ).scalar_one() == "UNVERIFIED"
