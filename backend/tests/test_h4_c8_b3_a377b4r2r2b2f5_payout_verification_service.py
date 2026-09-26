"""SQLite persistence contracts for payout destination verification service."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from itertools import count
from pathlib import Path
from threading import Barrier

import pytest
import sqlalchemy as sa
from sqlalchemy import event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.payout_destination_verification import (
    DestinationFreshness,
    VerificationOutcome,
    VerificationResult,
    VerificationResultState,
)
from app.db.base import Base
from app.models import (
    Group,
    Member,
    MemberPayoutDestination,
    PayoutDestinationVerificationAttempt,
    PayoutDestinationVerificationEvidence,
    User,
)
from app.services import payout_destination_verification as service


ROOT = Path(__file__).resolve().parents[1]
_IDS = count(83000)
_NOW = datetime(2026, 4, 5, 12, 30, tzinfo=timezone.utc)


def _load_migration(name: str, path: Path):
    spec = spec_from_file_location(name, path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("payout-verification-service") / "service.sqlite"
    value = sa.create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(value, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(value)
    with Session(value) as fixture_session:
        fixture_session.add(Group(id=83000, name="verification service fixtures"))
        fixture_session.commit()
    with value.begin() as connection:
        _load_migration(
            "verification_service_0101",
            ROOT / "alembic/versions/0101_member_payout_destination_a377b4r2.py",
        )._create_sqlite_guards(connection)
        _load_migration(
            "verification_service_0102",
            ROOT / "alembic/versions/0102_payout_verification_evidence_a377b4r3.py",
        )._create_sqlite_guards(connection)
    yield value
    value.dispose()


@pytest.fixture()
def db(engine):
    with Session(engine, expire_on_commit=False) as session:
        yield session
        session.rollback()


def _new_destination(db, *, version=1, status="UNVERIFIED", member_id=None):
    serial = next(_IDS)
    user_id = serial
    member_id = member_id or serial
    user = User(
        id=user_id,
        name=f"Synthetic member {serial}",
        email=f"verification-{serial}@example.test",
        cpf=f"100000{serial:05d}",
        password_hash="test-only",
    )
    db.add(user)
    db.flush()
    db.add(Member(id=member_id, user_id=user_id, group_id=83000))
    destination = MemberPayoutDestination(
        id=serial,
        member_id=member_id,
        version=version,
        key_type="CPF",
        encrypted_value="ciphertext-fixture-not-loaded-by-service",
        masked_value="***0000",
        verification_status=status,
        created_at=_NOW,
        created_by=user_id,
        revoked_at=_NOW if status == "REVOKED" else None,
        revoked_by=user_id if status == "REVOKED" else None,
        verified_at=_NOW if status == "VERIFIED" else None,
        verified_by=user_id if status == "VERIFIED" else None,
    )
    db.add(destination)
    db.flush()
    return destination, user_id


def _start(db, destination, *, key=None, provider="test-provider", actor=None):
    key = key or f"idem-{destination.id}"
    return service.start_verification_attempt(
        db,
        destination_id=destination.id,
        idempotency_key=key,
        provider_name=provider,
        requested_by=actor,
        requested_at=_NOW,
    )


def _result(
    attempt,
    *,
    state=VerificationResultState.FINAL,
    outcome=VerificationOutcome.CONFIRMED,
    provider=None,
    received_at=_NOW,
    provider_timestamp=None,
    event_id="event-1",
    digest=None,
):
    if state is VerificationResultState.PENDING:
        outcome = None
    return VerificationResult(
        attempt_id=attempt.attempt_id,
        state=state,
        outcome=outcome,
        provider_name=provider or attempt.provider_name,
        received_at=received_at,
        provider_request_id=f"request-{event_id}",
        provider_response_id=f"response-{event_id}",
        provider_event_id=event_id,
        provider_timestamp=provider_timestamp,
        reason_code="ownership.confirmed" if outcome is VerificationOutcome.CONFIRMED else None,
        evidence_digest=digest,
    )


def _record(db, result, *, status="NOT_CHECKED", method=None, checked_at=None):
    return service.record_verification_result(
        db,
        result=result,
        authenticity_status=status,
        authenticity_method=method,
        authenticity_checked_at=checked_at,
    )


def _replace_destination(db, old_destination, actor_id):
    old_destination.verification_status = "REVOKED"
    old_destination.revoked_at = _NOW + timedelta(minutes=1)
    old_destination.revoked_by = actor_id
    db.flush()
    new_id = next(_IDS)
    replacement = MemberPayoutDestination(
        id=new_id,
        member_id=old_destination.member_id,
        version=old_destination.version + 1,
        key_type=old_destination.key_type,
        encrypted_value="replacement-ciphertext-not-loaded",
        masked_value="***0001",
        verification_status="UNVERIFIED",
        created_at=_NOW + timedelta(minutes=1),
        created_by=actor_id,
    )
    db.add(replacement)
    db.flush()
    return replacement


def _mark_verified_fixture(db, destination, actor_id):
    connection = db.connection()
    for name in ("trg_mpd_insert_guard", "trg_mpd_update_guard", "trg_mpd_delete_guard"):
        connection.exec_driver_sql(f"DROP TRIGGER {name}")
    connection.execute(
        text("UPDATE member_payout_destinations SET verification_status='VERIFIED', verified_at=:at, verified_by=:actor WHERE id=:id"),
        {"at": _NOW, "actor": actor_id, "id": destination.id},
    )
    migration = _load_migration(
        "verification_service_0101_restore",
        ROOT / "alembic/versions/0101_member_payout_destination_a377b4r2.py",
    )
    migration._create_sqlite_guards(connection)
    db.expire(destination)
    db.flush()


def test_start_valid_generates_opaque_server_attempt_and_is_uncommitted(db):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, key="idem-key", actor=actor)
    attempt_id = attempt.attempt_id
    assert attempt.id > 0
    assert len(attempt.attempt_id) == 32
    assert attempt.attempt_id != "caller-choice"
    assert attempt.state == "REQUESTED"
    assert attempt.destination_version == 1
    assert db.in_transaction()
    db.rollback()
    with Session(db.bind) as independent:
        assert independent.query(PayoutDestinationVerificationAttempt).filter_by(
            attempt_id=attempt_id
        ).count() == 0


def test_start_same_idempotency_reuses_same_attempt(db):
    destination, actor = _new_destination(db)
    first = _start(db, destination, actor=actor)
    second = _start(db, destination, actor=actor)
    assert second.id == first.id
    assert db.query(PayoutDestinationVerificationAttempt).count() == 1


@pytest.mark.parametrize("mismatch", ["destination", "version"])
def test_start_idempotency_conflicts_across_destination_or_version(db, mismatch):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, key="idem-key", actor=actor)
    if mismatch == "destination":
        candidate, _ = _new_destination(db)
    else:
        candidate = _replace_destination(db, destination, actor)
    with pytest.raises(service.PayoutVerificationConflict) as caught:
        _start(db, candidate, key="idem-key", actor=actor)
    assert "idem-key" not in str(caught.value)
    assert attempt.destination_id != candidate.id


def test_start_same_idempotency_with_different_provider_conflicts(db):
    destination, actor = _new_destination(db)
    _start(db, destination, actor=actor)
    with pytest.raises(service.PayoutVerificationConflict):
        _start(db, destination, provider="other-provider", actor=actor)


def test_start_different_idempotency_conflicts_with_active_attempt(db):
    destination, actor = _new_destination(db)
    _start(db, destination, actor=actor)
    with pytest.raises(service.PayoutVerificationConflict):
        _start(db, destination, key="another-key", actor=actor)


@pytest.mark.parametrize("status", ["REVOKED", "VERIFIED"])
def test_start_rejects_non_unverified_destination(db, status):
    destination, actor = _new_destination(db)
    if status == "VERIFIED":
        _mark_verified_fixture(db, destination, actor)
    else:
        destination.verification_status = "REVOKED"
        destination.revoked_at = _NOW
        destination.revoked_by = actor
        db.flush()
    with pytest.raises(service.PayoutVerificationConflict):
        _start(db, destination, actor=actor)


def test_start_rejects_superseded_destination_version(db):
    old, actor = _new_destination(db)
    new = _replace_destination(db, old, actor)
    with pytest.raises(service.PayoutVerificationConflict):
        _start(db, old, key="old-version", actor=actor)
    assert new.version == old.version + 1


def test_start_validates_requested_by_but_allows_null(db):
    destination, _actor = _new_destination(db)
    with pytest.raises(service.PayoutVerificationNotFound):
        _start(db, destination, key="missing-actor", actor=999999999)
    attempt = _start(db, destination, key="system-source", actor=None)
    assert attempt.requested_by is None


def test_pending_results_advance_state_after_evidence_and_sequence(db):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    first = _record(db, _result(attempt, state=VerificationResultState.PENDING, event_id="pending-1"))
    assert first.sequence == 1
    assert first.result_state == "PENDING"
    assert attempt.state == "PENDING"
    second_result = _result(
        attempt,
        state=VerificationResultState.PENDING,
        received_at=_NOW + timedelta(seconds=1),
        event_id="pending-2",
    )
    second = _record(db, second_result)
    assert second.sequence == 2
    assert attempt.state == "PENDING"


@pytest.mark.parametrize("start_pending", [False, True])
def test_final_result_transitions_requested_or_pending_to_final(db, start_pending):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    if start_pending:
        _record(db, _result(attempt, state=VerificationResultState.PENDING, event_id="pending"))
        assert attempt.state == "PENDING"
    evidence = _record(db, _result(attempt, event_id="final"))
    assert evidence.result_state == "FINAL"
    assert attempt.state == "FINAL"


def test_confirmed_authentic_current_evidence_does_not_verify_destination(db):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    evidence = _record(
        db,
        _result(attempt),
        status="AUTHENTIC",
        method="authenticated-response",
        checked_at=_NOW,
    )
    db.flush()
    assert evidence.outcome == "CONFIRMED"
    assert evidence.authenticity_status == "AUTHENTIC"
    assert evidence.freshness == DestinationFreshness.CURRENT.value
    assert destination.verification_status == "UNVERIFIED"


def test_evidence_replay_is_idempotent_and_final_replay_remains_allowed(db):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    result = _result(attempt)
    first = _record(db, result)
    second = _record(db, result)
    assert first.id == second.id
    assert first.sequence == second.sequence == 1
    assert db.query(PayoutDestinationVerificationEvidence).count() == 1
    assert attempt.state == "FINAL"


@pytest.mark.parametrize("seconds", [-30, 30])
def test_same_delivery_replay_with_different_received_at_reuses_v1_evidence(db, seconds):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    original_result = _result(attempt)
    stored = _record(db, original_result)
    original_digest = stored.evidence_digest
    redelivery = replace(
        original_result,
        received_at=_NOW + timedelta(seconds=seconds),
        evidence_digest=original_digest,
    )

    # v1 remains the digest of the original persisted evidence, including its
    # local received_at; logical redelivery identity is a separate comparison.
    recalculated_v1 = service.compute_evidence_digest(
        attempt,
        redelivery,
        authenticity_status="NOT_CHECKED",
        authenticity_method=None,
        authenticity_checked_at=None,
        freshness=DestinationFreshness.CURRENT,
    )
    assert recalculated_v1 != original_digest

    replayed = _record(db, redelivery)
    assert replayed.id == stored.id
    assert replayed.evidence_digest == original_digest
    assert service._timestamp(replayed.received_at) == service._timestamp(_NOW)
    assert db.query(PayoutDestinationVerificationEvidence).count() == 1
    assert attempt.state == "FINAL"
    assert destination.verification_status == "UNVERIFIED"


@pytest.mark.parametrize(
    "changes",
    [
        {"outcome": VerificationOutcome.NOT_CONFIRMED},
        {"reason_code": "ownership.not_confirmed"},
        {"provider_timestamp": _NOW + timedelta(seconds=1)},
    ],
)
def test_same_provider_correlations_with_divergent_content_conflict(db, changes):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    original = _result(attempt)
    _record(db, original)
    changed = replace(original, **changes)
    with pytest.raises(service.PayoutVerificationConflict):
        _record(db, changed)
    assert db.query(PayoutDestinationVerificationEvidence).count() == 1


def test_changed_provider_event_id_is_not_hidden_as_same_delivery(db):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    _record(db, _result(attempt))
    with pytest.raises(service.PayoutVerificationConflict):
        _record(db, replace(_result(attempt), provider_event_id="different-event"))
    assert db.query(PayoutDestinationVerificationEvidence).count() == 1


def test_shared_provider_request_can_have_distinct_async_delivery_events(db):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    pending = replace(
        _result(attempt, state=VerificationResultState.PENDING, event_id="event-pending"),
        provider_request_id="request-shared",
        provider_response_id="response-pending",
    )
    _record(db, pending)
    final = replace(
        _result(attempt, event_id="event-final"),
        provider_request_id="request-shared",
        provider_response_id="response-final",
    )
    stored_final = _record(db, final)
    replay = _record(db, replace(final, received_at=_NOW + timedelta(seconds=10)))
    assert replay.id == stored_final.id
    assert db.query(PayoutDestinationVerificationEvidence).count() == 2


@pytest.mark.parametrize(
    ("status", "method"),
    [("INVALID", "authenticated-response"), ("UNVERIFIABLE", "authenticated-response"), ("AUTHENTIC", "different-method")],
)
def test_authenticity_status_or_method_change_is_not_hidden_as_replay(db, status, method):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    result = _result(attempt)
    _record(db, result, status="AUTHENTIC", method="authenticated-response", checked_at=_NOW)
    with pytest.raises(service.PayoutVerificationConflict):
        _record(db, result, status=status, method=method, checked_at=_NOW + timedelta(seconds=1))
    assert db.query(PayoutDestinationVerificationEvidence).count() == 1


def test_not_checked_to_authentic_is_not_hidden_as_replay(db):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    result = _result(attempt)
    _record(db, result)
    with pytest.raises(service.PayoutVerificationConflict):
        _record(
            db,
            result,
            status="AUTHENTIC",
            method="authenticated-response",
            checked_at=_NOW + timedelta(seconds=1),
        )
    assert db.query(PayoutDestinationVerificationEvidence).count() == 1


def test_authenticity_checked_at_is_local_metadata_for_same_authenticated_delivery(db):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    result = _result(attempt)
    stored = _record(
        db,
        result,
        status="AUTHENTIC",
        method="authenticated-response",
        checked_at=_NOW,
    )
    replayed = _record(
        db,
        replace(result, received_at=_NOW + timedelta(seconds=5)),
        status="AUTHENTIC",
        method="authenticated-response",
        checked_at=_NOW + timedelta(seconds=5),
    )
    assert replayed.id == stored.id
    assert service._timestamp(replayed.authenticity_checked_at) == service._timestamp(_NOW)
    assert service._timestamp(replayed.received_at) == service._timestamp(_NOW)
    assert db.query(PayoutDestinationVerificationEvidence).count() == 1


@pytest.mark.parametrize("replace_destination", [False, True])
def test_same_delivery_redelivery_after_revoke_or_replacement_preserves_original_evidence(
    db, replace_destination
):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    pending = _result(attempt, state=VerificationResultState.PENDING)
    original = _record(db, pending)

    if replace_destination:
        replacement = _replace_destination(db, destination, actor)
        expected_status = "STALE"
        assert replacement.verification_status == "UNVERIFIED"
    else:
        destination.verification_status = "REVOKED"
        destination.revoked_at = _NOW + timedelta(minutes=1)
        destination.revoked_by = actor
        db.flush()
        expected_status = "REVOKED"

    replay = _record(db, replace(pending, received_at=_NOW + timedelta(minutes=2)))
    assert replay.id == original.id
    assert replay.freshness == "CURRENT"
    assert db.query(PayoutDestinationVerificationEvidence).count() == 1

    # A genuinely new delivery is evaluated against the destination state at
    # processing time and records the new freshness, without consuming it.
    new_delivery = replace(
        _result(attempt, event_id="event-new"),
        provider_request_id="request-new",
        provider_response_id="response-new",
    )
    fresh_evidence = _record(db, new_delivery)
    assert fresh_evidence.id != original.id
    assert fresh_evidence.freshness == expected_status
    assert db.query(PayoutDestinationVerificationEvidence).count() == 2
    assert destination.verification_status == "REVOKED"
    assert (replacement.verification_status if replace_destination else None) == (
        "UNVERIFIED" if replace_destination else None
    )


def test_digest_mismatch_and_same_digest_conflicting_content_fail_closed(db):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    original = _result(attempt)
    evidence = _record(db, original)
    different = _result(attempt, event_id="different-event", digest=evidence.evidence_digest)
    with pytest.raises(service.PayoutVerificationConflict):
        _record(db, different)

    other_destination, other_actor = _new_destination(db)
    other_attempt = _start(db, other_destination, key="other-digest-key", actor=other_actor)
    incorrect = _result(other_attempt, digest="sha256:" + "0" * 64)
    with pytest.raises(service.PayoutVerificationConflict):
        _record(db, incorrect)


def test_correct_canonical_digest_is_accepted(db):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    result = _result(attempt)
    digest = service.compute_evidence_digest(
        attempt,
        result,
        authenticity_status="NOT_CHECKED",
        authenticity_method=None,
        authenticity_checked_at=None,
        freshness=DestinationFreshness.CURRENT,
    )
    evidence = _record(db, _result(attempt, digest=digest))
    assert evidence.evidence_digest == digest


def test_digest_is_deterministic_and_utc_offset_equivalent(db):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    utc_result = _result(
        attempt,
        received_at=datetime(2026, 4, 5, 12, 30, tzinfo=timezone.utc),
        provider_timestamp=datetime(2026, 4, 5, 12, 29, tzinfo=timezone.utc),
    )
    offset = timezone(timedelta(hours=3))
    offset_result = _result(
        attempt,
        received_at=datetime(2026, 4, 5, 15, 30, tzinfo=offset),
        provider_timestamp=datetime(2026, 4, 5, 15, 29, tzinfo=offset),
    )
    args = dict(
        authenticity_status="AUTHENTIC",
        authenticity_method="authenticated-response",
        freshness=DestinationFreshness.CURRENT,
    )
    left = service.compute_evidence_digest(
        attempt, utc_result, authenticity_checked_at=_NOW, **args
    )
    right = service.compute_evidence_digest(
        attempt,
        offset_result,
        authenticity_checked_at=datetime(2026, 4, 5, 15, 30, tzinfo=offset),
        **args,
    )
    assert left == right
    payload = service.canonical_evidence_payload(
        attempt,
        utc_result,
        authenticity_status="AUTHENTIC",
        authenticity_method="authenticated-response",
        authenticity_checked_at=_NOW,
        freshness=DestinationFreshness.CURRENT,
    )
    assert '"schema_version":"payout_verification_evidence_v1"' in payload
    for forbidden in ("encrypted_value", "pix_key", "normalized_key", "cpf", "100000"):
        assert forbidden not in payload


def test_digest_payload_has_all_optional_fields_as_null(db):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    result = VerificationResult(
        attempt_id=attempt.attempt_id,
        state=VerificationResultState.PENDING,
        outcome=None,
        provider_name=attempt.provider_name,
        received_at=_NOW,
    )
    payload = service.canonical_evidence_payload(
        attempt,
        result,
        authenticity_status="NOT_CHECKED",
        authenticity_method=None,
        authenticity_checked_at=None,
        freshness=DestinationFreshness.CURRENT,
    )
    assert '"provider_event_id":null' in payload
    assert '"provider_request_id":null' in payload
    assert '"provider_response_id":null' in payload
    assert '"provider_timestamp":null' in payload
    assert '"reason_code":null' in payload
    assert '"outcome":null' in payload


@pytest.mark.parametrize(
    ("status", "method", "checked"),
    [
        ("UNKNOWN", None, None),
        ("NOT_CHECKED", "method", _NOW),
        ("AUTHENTIC", None, _NOW),
        ("INVALID", "method", None),
        ("UNVERIFIABLE", "method", datetime(2026, 1, 1)),
    ],
)
def test_authenticity_rules_are_validated_before_insert(db, status, method, checked):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    with pytest.raises(service.PayoutVerificationConflict):
        _record(db, _result(attempt), status=status, method=method, checked_at=checked)
    assert db.query(PayoutDestinationVerificationEvidence).count() == 0


def test_replacement_between_attempt_and_result_is_stale_and_never_promotes(db):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    replacement = _replace_destination(db, destination, actor)
    evidence = _record(db, _result(attempt))
    assert evidence.freshness == "STALE"
    assert replacement.verification_status == "UNVERIFIED"
    assert destination.verification_status == "REVOKED"


def test_revoked_without_replacement_is_recorded_as_revoked(db):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    destination.verification_status = "REVOKED"
    destination.revoked_at = _NOW + timedelta(minutes=1)
    destination.revoked_by = actor
    db.flush()
    evidence = _record(db, _result(attempt))
    assert evidence.freshness == "REVOKED"
    assert destination.verification_status == "REVOKED"


def test_concurrent_revoke_is_refreshed_after_member_lock(db, monkeypatch):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    original_lock = service._lock_member
    original_projection = service._destination_projection
    projected_statuses = []

    def projection_with_status(session, destination_id):
        projection = original_projection(session, destination_id)
        projected_statuses.append(projection.verification_status if projection else None)
        return projection

    def revoke_before_lock(session, member_id):
        # Deterministically model a concurrent revoke after the locator read
        # and before this transaction acquires its member serialization lock.
        destination.verification_status = "REVOKED"
        destination.revoked_at = _NOW + timedelta(minutes=1)
        destination.revoked_by = actor
        session.flush()
        original_lock(session, member_id)

    monkeypatch.setattr(service, "_destination_projection", projection_with_status)
    monkeypatch.setattr(service, "_lock_member", revoke_before_lock)

    evidence = _record(db, _result(attempt))

    assert projected_statuses == ["UNVERIFIED", "REVOKED"]
    assert evidence.freshness == "REVOKED"
    assert destination.verification_status == "REVOKED"


def test_concurrent_replacement_is_refreshed_after_member_lock(db, monkeypatch):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    original_lock = service._lock_member
    original_projection = service._destination_projection
    projected_statuses = []
    replacements = []

    def projection_with_status(session, destination_id):
        projection = original_projection(session, destination_id)
        projected_statuses.append(projection.verification_status if projection else None)
        return projection

    def replace_before_lock(session, member_id):
        # Model replacement in the window between the locator read and lock.
        replacements.append(_replace_destination(session, destination, actor))
        original_lock(session, member_id)

    monkeypatch.setattr(service, "_destination_projection", projection_with_status)
    monkeypatch.setattr(service, "_lock_member", replace_before_lock)

    evidence = _record(db, _result(attempt))

    assert projected_statuses == ["UNVERIFIED", "REVOKED"]
    assert evidence.freshness == "STALE"
    assert replacements[0].version == destination.version + 1
    assert replacements[0].verification_status == "UNVERIFIED"
    assert destination.verification_status == "REVOKED"


def test_synthetic_verified_destination_is_not_unverified_and_result_is_audit_only(db):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    _mark_verified_fixture(db, destination, actor)
    evidence = _record(db, _result(attempt))
    assert evidence.freshness == "NOT_UNVERIFIED"
    assert destination.verification_status == "VERIFIED"


@pytest.mark.parametrize("replace", [True, False])
def test_stale_and_revoked_evidence_are_persisted_without_promotion(db, replace):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    if replace:
        current = _replace_destination(db, destination, actor)
        expected = "STALE"
    else:
        destination.verification_status = "REVOKED"
        destination.revoked_at = _NOW + timedelta(minutes=1)
        destination.revoked_by = actor
        db.flush()
        current = destination
        expected = "REVOKED"
    evidence = _record(db, _result(attempt))
    assert evidence.freshness == expected
    assert db.query(PayoutDestinationVerificationEvidence).filter_by(id=evidence.id).count() == 1
    if replace:
        assert current.verification_status == "UNVERIFIED"


@pytest.mark.parametrize("problem", ["provider", "attempt"])
def test_result_binding_mismatch_or_unknown_attempt_is_rejected(db, problem):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    if problem == "provider":
        result = _result(attempt, provider="wrong-provider")
        expected_error = service.PayoutVerificationConflict
    else:
        result = VerificationResult(
            attempt_id="unknown-attempt-id",
            state=VerificationResultState.FINAL,
            outcome=VerificationOutcome.CONFIRMED,
            provider_name=attempt.provider_name,
            received_at=_NOW,
        )
        expected_error = service.PayoutVerificationNotFound
    with pytest.raises(expected_error):
        _record(db, result)


def test_final_attempt_rejects_new_result_but_accepts_exact_replay(db):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    final = _result(attempt)
    stored = _record(db, final)
    assert _record(db, final).id == stored.id
    with pytest.raises(service.PayoutVerificationConflict):
        _record(db, _result(attempt, event_id="new-final-event"))


def test_sequences_start_at_one_and_increase_under_one_attempt(db):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    first = _record(db, _result(attempt, state=VerificationResultState.PENDING, event_id="p1"))
    second = _record(
        db,
        _result(attempt, state=VerificationResultState.PENDING, event_id="p2", received_at=_NOW + timedelta(seconds=2)),
    )
    assert (first.sequence, second.sequence) == (1, 2)


def test_safe_exceptions_do_not_echo_idempotency_key_or_plaintext(db):
    destination, actor = _new_destination(db)
    secret_key = "private-idempotency-value"
    _start(db, destination, key=secret_key, actor=actor)
    other, other_actor = _new_destination(db)
    with pytest.raises(service.PayoutVerificationConflict) as caught:
        _start(db, other, key=secret_key, actor=other_actor)
    assert secret_key not in str(caught.value)
    assert "ciphertext-fixture-not-loaded-by-service" not in str(caught.value)


def test_service_queries_never_select_destination_ciphertext(db):
    destination, actor = _new_destination(db)
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        if "member_payout_destinations" in statement.lower():
            statements.append(statement.lower())

    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        _start(db, destination, actor=actor)
        attempt = db.query(PayoutDestinationVerificationAttempt).one()
        _record(db, _result(attempt))
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert statements
    assert all("encrypted_value" not in statement for statement in statements)


def test_caller_controls_commit_and_service_keeps_transaction_usable(db):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    evidence = _record(db, _result(attempt))
    assert db.in_transaction()
    assert db.get(PayoutDestinationVerificationEvidence, evidence.id) is evidence
    db.commit()
    assert db.get(PayoutDestinationVerificationAttempt, attempt.id).state == "FINAL"


def test_service_sqlite_lock_uses_member_claim_and_postgresql_contract_uses_for_update():
    source = (ROOT / "app/services/payout_destination_verification.py").read_text()
    assert "connection.exec_driver_sql(\"BEGIN\")" in source
    assert ".values(id=Member.id)" in source
    assert ".with_for_update()" in source
    assert "FOR UPDATE" not in source.upper() or ".with_for_update()" in source


def test_verification_models_have_no_plaintext_pii_columns():
    forbidden = {
        "normalized_key", "pix_key", "cpf", "document", "holder_name",
        "recipient_name", "raw_payload", "provider_payload_json",
    }
    for model in (PayoutDestinationVerificationAttempt, PayoutDestinationVerificationEvidence):
        assert not (forbidden & {column.name.lower() for column in model.__table__.columns})


def test_same_idempotency_concurrently_returns_one_attempt(db, engine):
    destination, actor = _new_destination(db)
    destination_id = destination.id
    db.commit()
    barrier = Barrier(2)

    def worker():
        with Session(engine, expire_on_commit=False) as session:
            row = session.get(MemberPayoutDestination, destination_id)
            barrier.wait(timeout=10)
            attempt = _start(session, row, key="concurrent-same-idem", actor=actor)
            session.commit()
            return attempt.attempt_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        returned = list(pool.map(lambda _index: worker(), range(2)))
    assert returned[0] == returned[1]
    with Session(engine) as session:
        assert session.query(PayoutDestinationVerificationAttempt).filter_by(
            idempotency_key="concurrent-same-idem"
        ).count() == 1


def test_same_delivery_concurrently_replays_with_different_received_at(db, engine):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    attempt_id = attempt.attempt_id
    base_result = _result(attempt)
    db.commit()
    barrier = Barrier(2)

    def worker(seconds):
        with Session(engine, expire_on_commit=False) as session:
            barrier.wait(timeout=10)
            evidence = _record(
                session,
                replace(base_result, received_at=_NOW + timedelta(seconds=seconds)),
            )
            evidence_id = evidence.id
            session.commit()
            return evidence_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        evidence_ids = list(pool.map(worker, (1, 2)))
    assert evidence_ids[0] == evidence_ids[1]
    with Session(engine) as session:
        attempt_row = session.query(PayoutDestinationVerificationAttempt).filter_by(
            attempt_id=attempt_id
        ).one()
        rows = session.query(PayoutDestinationVerificationEvidence).filter_by(
            verification_attempt_id=attempt_row.id
        ).all()
        destination_row = session.get(MemberPayoutDestination, destination.id)
        assert len(rows) == 1
        assert service._timestamp(rows[0].received_at) in {
            service._timestamp(_NOW + timedelta(seconds=1)),
            service._timestamp(_NOW + timedelta(seconds=2)),
        }
        assert attempt_row.state == "FINAL"
        assert destination_row.verification_status == "UNVERIFIED"


def test_different_concurrent_attempts_for_same_destination_conflict(db, engine):
    destination, actor = _new_destination(db)
    destination_id = destination.id
    db.commit()
    barrier = Barrier(2)

    def worker(key):
        with Session(engine, expire_on_commit=False) as session:
            row = session.get(MemberPayoutDestination, destination_id)
            barrier.wait(timeout=10)
            try:
                attempt = _start(session, row, key=key, actor=actor)
                session.commit()
                return ("created", attempt.attempt_id)
            except service.PayoutVerificationConflict:
                session.rollback()
                return ("conflict", None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(worker, ("concurrent-key-a", "concurrent-key-b")))
    assert sorted(kind for kind, _attempt_id in outcomes) == ["conflict", "created"]


def test_start_does_not_return_or_read_sensitive_destination_values(db):
    destination, actor = _new_destination(db)
    attempt = _start(db, destination, actor=actor)
    assert not hasattr(attempt, "normalized_key")
    assert "ciphertext-fixture-not-loaded-by-service" not in repr(attempt)
