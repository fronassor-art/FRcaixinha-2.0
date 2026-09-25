"""Read-only payout readiness tests for A3.77B4-R3."""

from datetime import datetime, timezone
from decimal import Decimal
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    AuditLog,
    CycleAnnualClosing,
    CycleAnnualClosingPayoutObligation,
    CycleAnnualClosingReview,
    CycleAnnualClosingSnapshot,
    MemberPayoutDestination,
    User,
    WorkflowEvidenceIntegrityEvent,
)
from app.services.cycle_closing_payout_obligations import (
    PayoutObligationConflict,
    materialize_cycle_annual_closing_payout_obligations,
    verify_cycle_annual_closing_payout_obligations_read_only,
)
from app.services.cycle_closing_payout_readiness import (
    _destination_state,
    _review_financial_state,
    evaluate_cycle_annual_closing_payout_readiness,
)
from app.services.member_payout_destination import (
    create_unverified_destination,
    revoke_destination,
)


D = Decimal
ZERO = D("0.00")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(
        settings, "workflow_evidence_storage_root", str(tmp_path / "evidence"),
    )
    monkeypatch.setattr(
        settings, "payout_destination_encryption_key",
        Fernet.generate_key().decode("ascii"),
    )
    engine = sa.create_engine(
        f"sqlite:///{tmp_path / 'payout-readiness.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    versions = Path(__file__).parents[1] / "alembic/versions"
    for module_name, filename, creators in (
        (
            "a377b4r1_migration",
            "0100_cycle_payout_obligations_a377b4r1.py",
            ("_create_sqlite_insert_guard", "_create_sqlite_immutability_guards"),
        ),
        (
            "a377b4r2_migration",
            "0101_member_payout_destination_a377b4r2.py",
            ("_create_sqlite_guards",),
        ),
    ):
        spec = importlib.util.spec_from_file_location(module_name, versions / filename)
        migration = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(migration)
        with engine.begin() as connection:
            for creator in creators:
                getattr(migration, creator)(connection)

    with Session(engine, expire_on_commit=False) as session:
        session.info["engine"] = engine
        yield session
        session.rollback()
    engine.dispose()


def _obligation_helpers():
    test_path = Path(__file__).with_name(
        "test_h4_c8_b3_a377b4r1_payout_obligations.py"
    )
    spec = importlib.util.spec_from_file_location("a377b4r1_readiness_helpers", test_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _closed(db, *, materialize=True):
    helpers = _obligation_helpers()
    closing, review, snapshot = helpers._seed_and_close(db)
    if materialize:
        materialize_cycle_annual_closing_payout_obligations(
            db, closing_id=closing.id,
        )
        db.flush()
    return closing, review, snapshot, helpers


def _counts(db):
    table_names = (
        "cycle_annual_closings",
        "cycle_annual_closing_reviews",
        "cycle_annual_closing_snapshots",
        "cycle_annual_closing_payout_obligations",
        "member_payout_destinations",
        "audit_logs",
        "workflow_evidence_integrity_events",
    )
    return {
        name: db.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar_one()
        for name in table_names
    }


def _capture_dml(engine):
    statements = []

    def before_cursor_execute(_conn, _cursor, statement, _params, _context, _many):
        first = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
        if first in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    return statements, before_cursor_execute


def _set_review_memory(review, **changes):
    """Change only in-memory fixture state and recompute its pure test hashes."""
    import json

    from app.services.cycle_closing_workflow import _canonical, _digest, _review_memory

    reconciliation = json.loads(review.reconciliation_payload)
    field_map = {
        "actual_cash_balance": "actual_cash_balance",
        "reconciliation_difference": "reconciliation_difference",
        "required_liquidity": "required_liquidity",
        "liquidity_surplus": "liquidity_surplus",
    }
    for field, value in changes.items():
        setattr(review, field, value)
        reconciliation[field_map[field]] = str(value)
    review.reconciliation_payload = _canonical(reconciliation)
    review.reconciliation_hash = _digest(review.reconciliation_payload)
    review.review_hash = _digest(_canonical(_review_memory(review)))


def test_complete_readiness_is_read_only_and_zero_member_needs_no_destination(db):
    closing, _review, snapshot, _helpers = _closed(db)
    create_unverified_destination(
        db, member_id=1, key_type="CPF", value="529.982.247-25", actor_id=1,
    )
    db.flush()
    before_counts = _counts(db)
    before_status = (closing.status, closing.state_revision)
    engine = db.info["engine"]
    statements, listener = _capture_dml(engine)
    try:
        result = evaluate_cycle_annual_closing_payout_readiness(
            db, closing_id=closing.id,
        )
    finally:
        event.remove(engine, "before_cursor_execute", listener)

    assert result.ready is False
    assert result.reasons == ("DESTINATION_UNVERIFIED",)
    assert result.snapshot_id == snapshot.id
    assert result.snapshot_hash == snapshot.payload_hash
    assert result.participant_payout_liability == D("100.00")
    assert result.total_obligation_amount == D("100.00")
    assert result.obligation_count == 2
    assert result.positive_obligation_count == 1
    assert result.zero_obligation_count == 1
    assert result.verified_destination_count == 0
    assert result.blocked_destination_count == 1
    assert statements == []
    assert _counts(db) == before_counts
    db.refresh(closing)
    assert (closing.status, closing.state_revision) == before_status == ("CLOSED", closing.state_revision)


def test_missing_obligations_are_reported_without_materialization_or_writes(db):
    closing, _review, snapshot, _helpers = _closed(db, materialize=False)
    before_counts = _counts(db)
    engine = db.info["engine"]
    statements, listener = _capture_dml(engine)
    try:
        result = evaluate_cycle_annual_closing_payout_readiness(
            db, closing_id=closing.id,
        )
    finally:
        event.remove(engine, "before_cursor_execute", listener)

    assert result.ready is False
    assert result.reasons == ("OBLIGATIONS_INCOMPLETE",)
    assert result.obligation_count == 0
    assert _counts(db) == before_counts
    assert _obligation_helpers()._obligations(db, snapshot.id) == []
    assert statements == []
    assert closing.status == "CLOSED"


def test_read_only_obligation_verifier_raises_for_empty_set_without_writing(db):
    closing, _review, snapshot, _helpers = _closed(db, materialize=False)
    with pytest.raises(PayoutObligationConflict) as caught:
        verify_cycle_annual_closing_payout_obligations_read_only(
            db, closing_id=closing.id,
        )
    assert caught.value.reason_code == "OBLIGATIONS_INCOMPLETE"
    assert _obligation_helpers()._obligations(db, snapshot.id) == []


def test_partial_obligations_are_not_completed_by_readiness(db):
    closing, _review, snapshot, helpers = _closed(db, materialize=False)
    helpers._manual_obligation(db, snapshot, closing)
    db.flush()
    result = evaluate_cycle_annual_closing_payout_readiness(db, closing_id=closing.id)
    assert result.ready is False
    assert "OBLIGATIONS_INCOMPLETE" in result.reasons
    assert len(helpers._obligations(db, snapshot.id)) == 1


def test_one_cent_obligation_difference_is_exact_reconciliation_blocker(db):
    closing, _review, snapshot, helpers = _closed(db, materialize=False)
    helpers._manual_obligation(db, snapshot, closing, amount=D("99.99"))
    helpers._manual_obligation(
        db, snapshot, closing, member_id=2, participation_id=2,
        amount=ZERO, source_hash=snapshot.payload_hash,
    )
    db.flush()
    result = evaluate_cycle_annual_closing_payout_readiness(db, closing_id=closing.id)
    assert result.ready is False
    assert result.total_obligation_amount == D("99.99")
    assert result.reasons == ("OBLIGATION_RECONCILIATION_MISMATCH",)


def test_extra_obligation_fails_closed(db):
    closing, _review, snapshot, helpers = _closed(db, materialize=False)
    from app.models import CycleParticipation, Member

    db.add(Member(id=3, user_id=4, group_id=1))
    db.flush()
    db.add(CycleParticipation(id=3, cycle_id=1, member_id=3, status="ACTIVE"))
    db.flush()
    helpers._manual_obligation(
        db, snapshot, closing, member_id=3, participation_id=3,
        amount=ZERO, source_hash=snapshot.payload_hash,
    )
    db.flush()
    result = evaluate_cycle_annual_closing_payout_readiness(db, closing_id=closing.id)
    assert result.ready is False
    assert "OBLIGATIONS_INCOMPLETE" in result.reasons


def test_corrupt_snapshot_fails_closed_without_integrity_event_write(db):
    closing, _review, snapshot, _helpers = _closed(db)
    db.execute(
        text("UPDATE cycle_annual_closing_snapshots SET payload_hash=:hash WHERE id=:id"),
        {"hash": "f" * 64, "id": snapshot.id},
    )
    db.expire(snapshot)
    before = db.query(WorkflowEvidenceIntegrityEvent).count()
    result = evaluate_cycle_annual_closing_payout_readiness(db, closing_id=closing.id)
    assert result.ready is False
    assert "SNAPSHOT_INVALID" in result.reasons
    assert db.query(WorkflowEvidenceIntegrityEvent).count() == before


def test_nonclosed_missing_closing_and_missing_closing_reasons_are_deterministic(db):
    missing = evaluate_cycle_annual_closing_payout_readiness(db, closing_id=999)
    assert missing.ready is False
    assert missing.reasons == ("CLOSING_NOT_FOUND",)

    helpers = _obligation_helpers()
    closing, _review, _snapshot = helpers._seed_and_close(db, close=False)
    result = evaluate_cycle_annual_closing_payout_readiness(db, closing_id=closing.id)
    assert result.ready is False
    assert "CLOSING_NOT_CLOSED" in result.reasons
    assert "SNAPSHOT_MISSING" in result.reasons
    assert closing.status == "ASSESSING"


def test_snapshot_participant_with_missing_member_fails_closed_read_only(db):
    closing, _review, snapshot, helpers = _closed(db, materialize=False)
    helpers._rewrite_snapshot_payload(
        db, snapshot,
        lambda payload: payload["participants"][0].update(member_id=999),
    )
    result = evaluate_cycle_annual_closing_payout_readiness(db, closing_id=closing.id)
    assert result.ready is False
    assert "SNAPSHOT_INVALID" in result.reasons
    assert "OBLIGATIONS_INCOMPLETE" in result.reasons
    assert _obligation_helpers()._obligations(db, snapshot.id) == []


def test_destination_missing_and_revoked_are_distinguished(db):
    closing, _review, _snapshot, _helpers = _closed(db)
    missing = evaluate_cycle_annual_closing_payout_readiness(db, closing_id=closing.id)
    assert "DESTINATION_MISSING" in missing.reasons

    destination = create_unverified_destination(
        db, member_id=1, key_type="CPF", value="529.982.247-25", actor_id=1,
    )
    db.flush()
    revoke_destination(db, destination_id=destination.id, actor_id=1)
    db.flush()
    revoked = evaluate_cycle_annual_closing_payout_readiness(db, closing_id=closing.id)
    assert "DESTINATION_REVOKED" in revoked.reasons
    assert "DESTINATION_MISSING" not in revoked.reasons


def test_verified_classification_is_pure_and_requires_valid_lifecycle_metadata():
    valid = SimpleNamespace(
        verification_status="VERIFIED", verified_at=datetime.now(timezone.utc),
        verified_by=1, revoked_at=None, revoked_by=None,
    )
    assert _destination_state([valid]) == "VERIFIED"
    invalid = SimpleNamespace(
        verification_status="VERIFIED", verified_at=None, verified_by=None,
        revoked_at=None, revoked_by=None,
    )
    assert _destination_state([invalid]) == "INVALID"
    assert _destination_state([]) == "MISSING"


def test_review_hash_and_persisted_reconciliation_are_checked_without_file_verification(db):
    closing, review, _snapshot, _helpers = _closed(db)
    review.review_hash = "0" * 64
    result = evaluate_cycle_annual_closing_payout_readiness(db, closing_id=closing.id)
    assert result.ready is False
    assert "APPROVED_REVIEW_INVALID" in result.reasons


def test_one_cent_liquidity_shortfall_blocks_without_tolerance(db):
    closing, review, _snapshot, _helpers = _closed(db)
    _set_review_memory(
        review,
        actual_cash_balance=D("99.99"),
        liquidity_surplus=D("-0.01"),
    )
    result = evaluate_cycle_annual_closing_payout_readiness(db, closing_id=closing.id)
    assert result.ready is False
    assert result.actual_cash_balance == D("99.99")
    assert result.liquidity_surplus == D("-0.01")
    assert "INSUFFICIENT_LIQUIDITY" in result.reasons


def test_inconsistent_review_liquidity_equation_fails_closed(db):
    closing, review, _snapshot, _helpers = _closed(db)
    _set_review_memory(
        review,
        required_liquidity=D("100.01"),
        liquidity_surplus=D("-0.01"),
    )
    result = evaluate_cycle_annual_closing_payout_readiness(db, closing_id=closing.id)
    assert result.ready is False
    assert "APPROVED_REVIEW_INVALID" in result.reasons


@pytest.mark.parametrize(
    ("actual", "required", "surplus", "difference", "expected_valid", "expected_ready"),
    [
        ("100.00", "100.00", "0.00", "0.00", True, True),
        ("100.01", "100.00", "0.01", "0.00", True, True),
        ("99.99", "100.00", "-0.01", "0.00", True, False),
        ("100.00", "99.99", "0.00", "0.00", False, False),
        ("100.00", "100.00", "0.00", "0.01", False, False),
    ],
)
def test_review_liquidity_math_uses_exact_decimal_cents(
    actual, required, surplus, difference, expected_valid, expected_ready,
):
    review = SimpleNamespace(
        participant_payout_liability=D("100.00"),
        actual_cash_balance=D(actual),
        administration_fee=D("0.00"),
        reconciliation_difference=D(difference),
        required_liquidity=D(required),
        liquidity_surplus=D(surplus),
    )
    liability, actual_value, required_value, surplus_value, valid, ready = _review_financial_state(review)
    assert liability == D("100.00")
    assert actual_value == D(actual)
    assert required_value == D(required)
    assert surplus_value == D(surplus)
    assert valid is expected_valid
    assert ready is expected_ready


def test_unknown_or_duplicate_destination_state_fails_closed():
    unknown = SimpleNamespace(
        verification_status="FUTURE", verified_at=None, verified_by=None,
        revoked_at=None, revoked_by=None,
    )
    assert _destination_state([unknown]) == "INVALID"
    unverified = SimpleNamespace(
        verification_status="UNVERIFIED", verified_at=None, verified_by=None,
        revoked_at=None, revoked_by=None,
    )
    assert _destination_state([unverified, unverified]) == "AMBIGUOUS"
