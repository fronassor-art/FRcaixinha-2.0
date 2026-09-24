"""A3.77B3 R1 review, approval and official close gates."""

from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from copy import deepcopy
from io import BytesIO
import json
import hashlib
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi import UploadFile
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    AuditLog, Contribution, Cycle, CycleAnnualClosingCashEvidence, CycleAnnualClosingReview,
    CycleAnnualClosingSnapshot, CycleParticipation, Group, LedgerEntry, Member, User,
    OperationalWorkflowTask, OperationalWorkflowOrchestration, WorkflowExecutionEvidence,
    WorkflowExecutionEvidenceFile,
)
from app.services.cycle_closing_persistence import (
    create_or_get_cycle_annual_closing, persist_approved_cycle_closing_snapshot,
)
from app.services.cycle_closing_workflow import (
    ClosingWorkflowConflict, StaleClosingReview, approve_cycle_closing_review,
    prepare_cycle_closing_review, revalidate_cycle_closing_review,
    record_cycle_annual_closing_cash_evidence,
)
from app.services.workflow_evidence_storage_v068 import _storage_path
from app.services.workflow_evidence_storage_v068 import upload_file
from app.services.ledger import post_entry, reverse_entry


D = Decimal
CUTOFF = datetime(2027, 12, 10, 18, tzinfo=timezone.utc)
PAID_AT = datetime(2027, 12, 9, 18, tzinfo=timezone.utc)
CASH_HASH = "a" * 64


@pytest.fixture(autouse=True)
def isolated_workflow_evidence_storage(tmp_path, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "workflow_evidence_storage_root", str(tmp_path / "workflow-evidence"))


@pytest.fixture()
def db(isolated_workflow_evidence_storage):
    engine = sa.create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
        session.rollback()
    engine.dispose()


def seed(db, *, paid=False, ledger_cash=D("0.00"), reserve=D("0.00")):
    db.add_all([
        User(id=1, name="Member", email="member@example.test", cpf="00000000000001",
             password_hash="unused", role="USER"),
        User(id=2, name="Master", email="master@example.test", cpf="00000000000002",
             password_hash="unused", role="ADMIN", is_master=True),
        User(id=3, name="Admin", email="admin@example.test", cpf="00000000000003",
             password_hash="unused", role="ADMIN"),
        User(id=4, name="Inactive", email="inactive@example.test", cpf="00000000000004",
             password_hash="unused", role="ADMIN", is_active=False),
        Group(id=1, name="Group", min_cash_reserve=reserve),
    ])
    db.flush()
    db.add(Member(id=1, user_id=1, group_id=1))
    db.add(Cycle(id=1, start_date=date(2026, 12, 10), entry_deadline=date(2027, 1, 10),
                 closing_reference_date=date(2027, 12, 10), monthly_amount=D("150.00"),
                 months=12, max_quotas=50, status="OPEN"))
    db.flush()
    db.add(CycleParticipation(id=1, cycle_id=1, member_id=1, status="ACTIVE"))
    if paid:
        db.add(Contribution(id=1, member_id=1, cycle_id=1, competence=date(2027, 1, 1),
                            amount=D("100.00"), status="PAID", paid_amount=D("100.00"),
                            paid_at=PAID_AT))
    db.flush()
    if ledger_cash:
        post_entry(db, "CAIXINHA", "CREDIT", ledger_cash, "TEST_CASH", "1")
        db.flush()
    task = OperationalWorkflowTask(action_code="CLOSING_EVIDENCE", status="OPEN", priority="MEDIUM", created_by=3)
    db.add(task)
    db.flush()
    db.add(OperationalWorkflowOrchestration(task_id=task.id, priority="MEDIUM", sla_status="ON_TRACK",
                                          execution_state="IN_EXECUTION", started_by=3))
    evidence = WorkflowExecutionEvidence(task_id=task.id, added_by=3, evidence_type="ATTACHMENT",
        title="Cash position", content="stored evidence", content_hash=hashlib.sha256(b"stored evidence").hexdigest())
    db.add(evidence)
    db.flush()
    closing = create_or_get_cycle_annual_closing(db, cycle_id=1, created_by=3)
    return closing


def _cash_evidence_parent(db):
    return db.execute(
        select(WorkflowExecutionEvidence)
        .join(OperationalWorkflowTask, OperationalWorkflowTask.id == WorkflowExecutionEvidence.task_id)
        .where(OperationalWorkflowTask.action_code == "CLOSING_EVIDENCE")
    ).scalar_one()


def _upload_cash_test_file(db, evidence_parent, *, name, payload):
    task = db.get(OperationalWorkflowTask, evidence_parent.task_id)
    return upload_file(
        db, task, evidence_parent, 3,
        UploadFile(filename=name, file=BytesIO(payload), headers={"content-type": "text/plain"}),
    )


def _ledger_cutoff_after_rows(db):
    """Use a cutoff later than the persisted, hash-covered test entries."""
    db.flush()
    rows = db.scalars(select(LedgerEntry)).all()
    if not rows:
        return CUTOFF
    latest = max(
        row.created_at.replace(tzinfo=timezone.utc)
        if row.created_at.tzinfo is None else row.created_at.astimezone(timezone.utc)
        for row in rows
    )
    return max(CUTOFF, latest + timedelta(microseconds=1))


def prepare(db, closing, *, cash=D("0.00"), evidence_hash=CASH_HASH,
            revision=None, cutoff=CUTOFF, actor_id=3):
    evidence_parent = _cash_evidence_parent(db)
    original_name = f"cash-{evidence_hash}.txt"
    file_row = db.execute(select(WorkflowExecutionEvidenceFile).where(
        WorkflowExecutionEvidenceFile.evidence_id == evidence_parent.id,
        WorkflowExecutionEvidenceFile.original_name == original_name,
    )).scalar_one_or_none()
    if file_row is None:
        payload = f"cash statement for annual close {evidence_hash}".encode()
        file_row = _upload_cash_test_file(db, evidence_parent, name=original_name, payload=payload)
    cash_evidence = record_cycle_annual_closing_cash_evidence(
        db, closing_id=closing.id, file_id=file_row.id,
        declared_cash_balance=cash, observed_at=cutoff, closing_cutoff_at=cutoff, attested_by=2,
    )
    return prepare_cycle_closing_review(
        db, closing_id=closing.id,
        expected_state_revision=closing.state_revision if revision is None else revision,
        closing_cutoff_at=cutoff, cash_evidence_id=cash_evidence.id, actor_id=actor_id,
    )


def approve(db, closing, review, *, actor_id=2, revision=None):
    return approve_cycle_closing_review(
        db, closing_id=closing.id, review_id=review.id,
        expected_state_revision=closing.state_revision if revision is None else revision,
        actor_id=actor_id,
    )


def test_valid_review_approval_close_and_idempotent_retries(db):
    closing = seed(db, paid=True, ledger_cash=D("100.00"))
    review = prepare(db, closing, cash=D("100.00"))
    assert closing.status == "READY_FOR_REVIEW"
    assert closing.state_revision == 1
    assert review.review_version == 1
    assert review.reconciliation_difference == D("0.00")
    assert review.participant_payout_liability == D("100.00")
    assert review.required_liquidity == D("100.00")
    assert review.liquidity_surplus == D("0.00")
    assert prepare(db, closing, cash=D("100.00")).id == review.id
    assert db.scalar(select(sa.func.count()).select_from(CycleAnnualClosingReview)) == 1
    approve(db, closing, review)
    assert closing.approved_review_id == review.id
    assert closing.approved_by == 2 and closing.state_revision == 2
    snapshot = persist_approved_cycle_closing_snapshot(
        db, closing_id=closing.id, closing_cutoff_at=CUTOFF,
        expected_state_revision=closing.state_revision,
    )
    assert closing.status == "CLOSED" and closing.state_revision == 3
    assert snapshot.created_by == 2
    assert persist_approved_cycle_closing_snapshot(
        db, closing_id=closing.id, expected_state_revision=closing.state_revision,
    ).id == snapshot.id
    assert db.scalar(select(sa.func.count()).select_from(CycleAnnualClosingSnapshot)) == 1
    audit_rows = db.scalars(select(AuditLog).where(AuditLog.action.in_({
        "CYCLE_ANNUAL_CLOSING_REVIEW", "CYCLE_ANNUAL_CLOSING_APPROVED",
        "CYCLE_ANNUAL_CLOSING_CLOSED",
    })).order_by(AuditLog.id)).all()
    assert len(audit_rows) == 3
    expected = [
        ("CYCLE_ANNUAL_CLOSING_REVIEW", "ASSESSING", "READY_FOR_REVIEW", 0, 1, 3),
        ("CYCLE_ANNUAL_CLOSING_APPROVED", "READY_FOR_REVIEW", "MASTER_APPROVED", 1, 2, 2),
        ("CYCLE_ANNUAL_CLOSING_CLOSED", "MASTER_APPROVED", "CLOSED", 2, 3, 2),
    ]
    for row, (action, from_status, to_status, revision_before, revision_after, actor) in zip(audit_rows, expected):
        details = json.loads(row.details)
        assert row.action == action
        assert row.actor_user_id == actor
        assert details["closing_id"] == closing.id
        assert details["cycle_id"] == closing.cycle_id
        assert details["review_id"] == review.id
        assert details["review_version"] == review.review_version
        assert details["from_status"] == from_status
        assert details["to_status"] == to_status
        assert details["revision_before"] == revision_before
        assert details["revision_after"] == revision_after
        assert details["cutoff"] == CUTOFF.isoformat().replace("+00:00", "Z")
        assert details["calculation_hash"] == review.calculation_hash
        assert details["reconciliation_hash"] == review.reconciliation_hash
        assert details["cash_evidence_hash"] == review.cash_evidence_hash
        assert details["actor"] == actor


def test_different_cash_evidence_creates_a_new_review_version(db):
    closing = seed(db, paid=True, ledger_cash=D("100.00"))
    first = prepare(db, closing, cash=D("100.00"))
    second = prepare(db, closing, cash=D("100.00"), evidence_hash="b" * 64)
    assert second.id != first.id
    assert second.cash_evidence_id != first.cash_evidence_id
    assert second.review_version == first.review_version + 1
    assert second.process_revision == closing.state_revision == 2
    assert db.scalar(select(sa.func.count()).select_from(CycleAnnualClosingReview)) == 2


def test_closed_snapshot_retry_rejects_stale_revision_without_mutation(db):
    closing = seed(db, paid=True, ledger_cash=D("100.00"))
    review = prepare(db, closing, cash=D("100.00"))
    approve(db, closing, review)
    revision_before_close = closing.state_revision
    snapshot = persist_approved_cycle_closing_snapshot(
        db, closing_id=closing.id, expected_state_revision=revision_before_close,
    )
    closed_revision = closing.state_revision

    with pytest.raises(ClosingWorkflowConflict, match="stale"):
        persist_approved_cycle_closing_snapshot(
            db, closing_id=closing.id, expected_state_revision=revision_before_close,
        )

    assert closing.state_revision == closed_revision
    assert db.scalar(select(sa.func.count()).select_from(CycleAnnualClosingSnapshot)) == 1
    assert persist_approved_cycle_closing_snapshot(
        db, closing_id=closing.id, expected_state_revision=closed_revision,
    ).id == snapshot.id
    assert closing.state_revision == closed_revision


@pytest.mark.parametrize("actual", [D("100.01"), D("99.99")])
def test_one_cent_reconciliation_difference_blocks(db, actual):
    closing = seed(db, paid=True, ledger_cash=D("100.00"))
    with pytest.raises(ValueError, match="differs from zero"):
        prepare(db, closing, cash=actual)
    assert closing.status == "ASSESSING"
    assert db.scalar(select(sa.func.count()).select_from(CycleAnnualClosingReview)) == 0


@pytest.mark.parametrize("seconds", [1, -1])
def test_cash_evidence_observation_must_equal_cutoff_exactly(db, seconds):
    closing = seed(db)
    evidence_parent = _cash_evidence_parent(db)
    stored_file = _upload_cash_test_file(
        db, evidence_parent, name=f"cash-time-{seconds}.txt", payload=b"cash time evidence",
    )
    with pytest.raises(ValueError, match="observation instant"):
        record_cycle_annual_closing_cash_evidence(
            db, closing_id=closing.id, file_id=stored_file.id, declared_cash_balance=D("0.00"),
            observed_at=CUTOFF + timedelta(seconds=seconds), closing_cutoff_at=CUTOFF, attested_by=2,
        )


def test_cash_evidence_timezone_equivalent_master_auth_and_immutability(db):
    closing = seed(db)
    evidence_parent = _cash_evidence_parent(db)
    stored_file = _upload_cash_test_file(
        db, evidence_parent, name="cash-immutable.txt", payload=b"cash evidence immutable",
    )
    equivalent = CUTOFF.astimezone(__import__("datetime").timezone(__import__("datetime").timedelta(hours=-3)))
    cash = record_cycle_annual_closing_cash_evidence(
        db, closing_id=closing.id, file_id=stored_file.id, declared_cash_balance=D("0.00"),
        observed_at=equivalent, closing_cutoff_at=CUTOFF, attested_by=2,
    )
    with pytest.raises(ValueError, match="Master"):
        record_cycle_annual_closing_cash_evidence(
            db, closing_id=closing.id, file_id=stored_file.id, declared_cash_balance=D("0.00"),
            observed_at=CUTOFF, closing_cutoff_at=CUTOFF, attested_by=3,
        )
    with pytest.raises(ValueError, match="active"):
        record_cycle_annual_closing_cash_evidence(
            db, closing_id=closing.id, file_id=stored_file.id, declared_cash_balance=D("0.00"),
            observed_at=CUTOFF, closing_cutoff_at=CUTOFF, attested_by=4,
        )
    cash.declared_cash_balance = D("1.00")
    with pytest.raises(RuntimeError, match="imutável"):
        db.flush()
    db.rollback()


def test_cash_evidence_missing_or_revoked_file_is_rejected(db):
    closing = seed(db)
    with pytest.raises(ValueError, match="file"):
        record_cycle_annual_closing_cash_evidence(
            db, closing_id=closing.id, file_id=999, declared_cash_balance=D("0.00"),
            observed_at=CUTOFF, closing_cutoff_at=CUTOFF, attested_by=2,
        )
    evidence_parent = _cash_evidence_parent(db)
    stored_file = _upload_cash_test_file(
        db, evidence_parent, name="cash-revoked.txt", payload=b"cash evidence",
    )
    stored_file.revoked_at = CUTOFF
    db.flush()
    with pytest.raises(ValueError, match="revoked"):
        record_cycle_annual_closing_cash_evidence(
            db, closing_id=closing.id, file_id=stored_file.id, declared_cash_balance=D("0.00"),
            observed_at=CUTOFF, closing_cutoff_at=CUTOFF, attested_by=2,
        )


def test_cash_evidence_rehashes_real_stored_bytes(db):
    closing = seed(db)
    evidence_parent = _cash_evidence_parent(db)
    payload = b"original cash file"
    stored_file = _upload_cash_test_file(
        db, evidence_parent, name="cash-tampered.txt", payload=payload,
    )
    path = _storage_path(stored_file.storage_key)
    path.write_bytes(b"altered bytes")
    with pytest.raises(ValueError, match="hash verification"):
        record_cycle_annual_closing_cash_evidence(
            db, closing_id=closing.id, file_id=stored_file.id, declared_cash_balance=D("0.00"),
            observed_at=CUTOFF, closing_cutoff_at=CUTOFF, attested_by=2,
        )


def test_revoked_cash_file_after_review_blocks_master_approval(db):
    closing = seed(db)
    review = prepare(db, closing)
    stored_file = db.execute(select(WorkflowExecutionEvidenceFile).where(
        WorkflowExecutionEvidenceFile.original_name == f"cash-{CASH_HASH}.txt"
    )).scalar_one()
    stored_file.revoked_at = CUTOFF
    db.flush()
    with pytest.raises(StaleClosingReview, match="stale"):
        approve(db, closing, review)
    assert closing.status == "READY_FOR_REVIEW"
    assert closing.approved_review_id is None


def test_ledger_balance_uses_caixinha_and_known_cash_movement_off_account_fails(db):
    from app.services.cycle_closing_workflow import _ledger_cash_at_cutoff
    post_entry(db, "OTHER", "CREDIT", D("50.00"), "UNKNOWN_TEST_TYPE", "other")
    post_entry(db, "CAIXINHA", "CREDIT", D("10.00"), "UNKNOWN_TEST_TYPE", "cash")
    cutoff = _ledger_cutoff_after_rows(db)
    balance, _trace = _ledger_cash_at_cutoff(db, cutoff)
    assert balance == D("10.00")
    post_entry(db, "OTHER", "CREDIT", D("1.00"), "EXPENSE", "known-cash-outside")
    cutoff = _ledger_cutoff_after_rows(db)
    with pytest.raises(ValueError, match="outside CAIXINHA"):
        _ledger_cash_at_cutoff(db, cutoff)


def test_ledger_compensating_reversal_is_netted_once(db):
    from app.services.cycle_closing_workflow import _ledger_cash_at_cutoff
    original = post_entry(db, "CAIXINHA", "CREDIT", D("15.00"), "TEST_CASH", "reversible")
    db.flush()
    assert original.id is not None
    original_created_at = original.created_at

    reversal = reverse_entry(db, original, "test reversal")
    db.flush()
    assert reversal.id is not None
    assert reversal.reversal_of_id == original.id
    assert original.created_at == original_created_at
    assert reversal.created_at >= original.created_at
    cutoff = _ledger_cutoff_after_rows(db)
    assert cutoff >= reversal.created_at.replace(tzinfo=timezone.utc) if reversal.created_at.tzinfo is None else cutoff >= reversal.created_at.astimezone(timezone.utc)

    balance, trace = _ledger_cash_at_cutoff(db, cutoff)
    assert [row["id"] for row in trace] == [original.id, reversal.id]
    assert trace[0]["created_at"] <= trace[1]["created_at"] <= cutoff.isoformat().replace("+00:00", "Z")
    assert trace[0]["reversal_of_id"] is None
    assert trace[1]["reversal_of_id"] == original.id
    assert balance == D("0.00")


@pytest.mark.parametrize("column,value", [
    ("entry_hash", ""), ("previous_hash", "f" * 64), ("entry_hash", "e" * 64),
])
def test_ledger_global_prefix_hash_corruption_blocks_review(db, column, value):
    from app.services.cycle_closing_workflow import _ledger_cash_at_cutoff
    entry = post_entry(db, "CAIXINHA", "CREDIT", D("1.00"), "UNKNOWN_TEST_TYPE", "chain")
    cutoff = _ledger_cutoff_after_rows(db)
    db.execute(sa.update(LedgerEntry).where(LedgerEntry.id == entry.id).values({column: value}))
    db.expire_all()
    with pytest.raises(ValueError, match="ledger (integrity|chain)"):
        _ledger_cash_at_cutoff(db, cutoff)


def test_duplicate_compensating_reversal_is_rejected(db):
    from app.services.cycle_closing_workflow import _ledger_cash_at_cutoff
    original = post_entry(db, "CAIXINHA", "CREDIT", D("3.00"), "UNKNOWN_TEST_TYPE", "dup")
    db.flush()
    assert original.id is not None

    first_reversal = reverse_entry(db, original, "first reversal")
    db.flush()
    assert first_reversal.id is not None
    assert first_reversal.reversal_of_id == original.id

    with pytest.raises(ValueError, match="já possui reversão"):
        reverse_entry(db, original, "second reversal")

    entries = db.scalars(select(LedgerEntry).order_by(LedgerEntry.id)).all()
    assert [entry.id for entry in entries] == [original.id, first_reversal.id]
    assert sum(entry.reversal_of_id == original.id for entry in entries) == 1
    balance, trace = _ledger_cash_at_cutoff(db, _ledger_cutoff_after_rows(db))
    assert [entry["id"] for entry in trace] == [original.id, first_reversal.id]
    assert balance == D("0.00")


def test_liquidity_deficit_blocks_and_exact_cash_passes(db):
    closing = seed(db, paid=True, ledger_cash=D("50.00"))
    with pytest.raises(ValueError, match="insufficient"):
        prepare(db, closing, cash=D("50.00"))
    post_entry(db, "CAIXINHA", "CREDIT", D("50.00"), "TEST_CASH", "2")
    db.flush()
    assert prepare(db, closing, cash=D("100.00")).liquidity_surplus == D("0.00")


def test_ledger_reversal_is_counted_as_compensating_entry_once(db):
    closing = seed(db, ledger_cash=D("10.00"))
    original = db.scalar(select(LedgerEntry).where(LedgerEntry.reversal_of_id.is_(None)))
    reverse_entry(db, original, "cash correction")
    db.flush()
    review = prepare(db, closing, cash=D("0.00"))
    assert review.ledger_cash_balance == D("0.00")


@pytest.mark.parametrize("cash", [D("-1.00"), D("0.001")])
def test_cash_evidence_validation(db, cash):
    closing = seed(db)
    with pytest.raises(ValueError):
        prepare(db, closing, cash=cash)


@pytest.mark.parametrize("field", ["source_gaps", "unavailable_result_sources"])
def test_preview_gaps_block_review(db, monkeypatch, field):
    closing = seed(db)
    from app.services import cycle_closing_workflow as workflow
    original = workflow.preview_cycle_closing

    def preview_with_gap(*args, **kwargs):
        result = deepcopy(original(*args, **kwargs))
        result[field] = [{"code": "MISSING", "source": "test"}]
        return result

    monkeypatch.setattr(workflow, "preview_cycle_closing", preview_with_gap)
    with pytest.raises(ValueError, match="blocking source gaps"):
        prepare(db, closing)
    assert db.scalar(select(sa.func.count()).select_from(CycleAnnualClosingReview)) == 0


def test_min_cash_reserve_does_not_affect_review_or_hash(db):
    closing = seed(db, paid=True, ledger_cash=D("100.00"), reserve=D("0.00"))
    review = prepare(db, closing, cash=D("100.00"))
    original = (review.reconciliation_hash, review.review_hash, review.required_liquidity,
                review.participant_payout_liability, review.administration_fee)
    db.get(Group, 1).min_cash_reserve = D("999999.00")
    db.flush()
    assert prepare(db, closing, cash=D("100.00")).id == review.id
    assert original == (review.reconciliation_hash, review.review_hash, review.required_liquidity,
                        review.participant_payout_liability, review.administration_fee)


def test_review_canonical_hash_is_repeatable(db):
    closing = seed(db)
    review = prepare(db, closing)
    from app.services.cycle_closing_workflow import _canonical, _digest, _review_memory
    assert review.review_hash == _digest(_canonical(_review_memory(review)))
    assert review.review_hash == _digest(_canonical(_review_memory(review)))
    assert revalidate_cycle_closing_review(db, review)["calculation_hash"] == review.calculation_hash


def test_new_review_is_append_only_and_old_review_cannot_be_approved(db):
    closing = seed(db)
    first = prepare(db, closing)
    second = prepare(db, closing, evidence_hash="b" * 64)
    assert second.id != first.id and second.review_version == 2
    assert second.process_revision == 2 and closing.state_revision == 2
    assert first.review_hash != second.review_hash
    with pytest.raises(ClosingWorkflowConflict, match="not current"):
        approve(db, closing, first)
    approve(db, closing, second)


@pytest.mark.parametrize("actor_id", [1, 3, 4])
def test_only_active_master_approves(db, actor_id):
    closing = seed(db)
    review = prepare(db, closing)
    with pytest.raises(ValueError):
        approve(db, closing, review, actor_id=actor_id)
    assert closing.status == "READY_FOR_REVIEW"


def test_database_rejects_inactive_master(db):
    seed(db)
    inactive = db.get(User, 4)
    inactive.is_master = True
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_stale_revision_and_competing_prepare_or_approval(db):
    closing = seed(db)
    first = prepare(db, closing)
    with pytest.raises(ClosingWorkflowConflict, match="stale"):
        prepare(db, closing, revision=0)
    approve(db, closing, first)
    with pytest.raises(ClosingWorkflowConflict, match="stale"):
        approve(db, closing, first, revision=1)


@pytest.mark.parametrize("operation", ["prepare", "approve"])
def test_two_sessions_claim_one_revision(tmp_path, monkeypatch, operation):
    from app.core.config import settings
    monkeypatch.setattr(settings, "workflow_evidence_storage_root", str(tmp_path / "evidence"))
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'closing_claim.db'}")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as setup:
        closing = seed(setup)
        if operation == "approve":
            review = prepare(setup, closing)
        setup.commit()
        closing_id = closing.id
        review_id = review.id if operation == "approve" else None
    with Session(engine, expire_on_commit=False) as first, Session(engine, expire_on_commit=False) as second:
        stale = second.get(type(closing), closing_id)
        expected = stale.state_revision
        current = first.get(type(closing), closing_id)
        if operation == "prepare":
            prepare(first, current)
        else:
            approve_cycle_closing_review(
                first, closing_id=closing_id, review_id=review_id,
                expected_state_revision=expected, actor_id=2,
            )
        first.commit()
        with pytest.raises(ClosingWorkflowConflict):
            if operation == "prepare":
                prepare(second, stale, revision=expected)
            else:
                approve_cycle_closing_review(
                    second, closing_id=closing_id, review_id=review_id,
                    expected_state_revision=expected, actor_id=2,
                )
        second.rollback()
    engine.dispose()


def test_ledger_change_invalidates_approval_even_with_same_net_cash(db):
    closing = seed(db)
    review = prepare(db, closing)
    post_entry(db, "CAIXINHA", "CREDIT", D("1.00"), "TEST", "credit")
    db.flush()
    post_entry(db, "CAIXINHA", "DEBIT", D("1.00"), "TEST", "debit")
    db.flush()
    with pytest.raises(StaleClosingReview):
        approve(db, closing, review)
    assert closing.status == "READY_FOR_REVIEW"


def test_calculation_change_invalidates_approval(db, monkeypatch):
    closing = seed(db, paid=True, ledger_cash=D("100.00"))
    review = prepare(db, closing, cash=D("100.00"))
    from app.services import cycle_closing_workflow as workflow
    original = workflow.preview_cycle_closing

    def changed_preview(*args, **kwargs):
        result = deepcopy(original(*args, **kwargs))
        result["calculation_hash"] = "b" * 64
        return result

    monkeypatch.setattr(workflow, "preview_cycle_closing", changed_preview)
    with pytest.raises(StaleClosingReview):
        approve(db, closing, review)
    assert closing.status == "READY_FOR_REVIEW"


def test_approved_review_revalidates_before_snapshot(db):
    closing = seed(db, paid=True, ledger_cash=D("100.00"))
    review = prepare(db, closing, cash=D("100.00"))
    approve(db, closing, review)
    post_entry(db, "CAIXINHA", "CREDIT", D("1.00"), "TEST", "late")
    db.flush()
    with pytest.raises(StaleClosingReview):
        persist_approved_cycle_closing_snapshot(
            db, closing_id=closing.id, expected_state_revision=closing.state_revision,
        )
    assert closing.status == "MASTER_APPROVED" and closing.state_revision == 2
    assert db.scalar(select(sa.func.count()).select_from(CycleAnnualClosingSnapshot)) == 0


def test_calculation_change_after_approval_blocks_snapshot(db, monkeypatch):
    closing = seed(db, paid=True, ledger_cash=D("100.00"))
    review = prepare(db, closing, cash=D("100.00"))
    approve(db, closing, review)
    from app.services import cycle_closing_workflow as workflow
    original = workflow.preview_cycle_closing

    def changed_preview(*args, **kwargs):
        result = deepcopy(original(*args, **kwargs))
        result["calculation_hash"] = "b" * 64
        return result

    monkeypatch.setattr(workflow, "preview_cycle_closing", changed_preview)
    with pytest.raises(StaleClosingReview):
        persist_approved_cycle_closing_snapshot(
            db, closing_id=closing.id, expected_state_revision=closing.state_revision,
        )
    assert closing.status == "MASTER_APPROVED"
    assert db.scalar(select(sa.func.count()).select_from(CycleAnnualClosingSnapshot)) == 0


def test_direct_master_approved_without_review_cannot_close(db):
    closing = seed(db)
    closing.status = "MASTER_APPROVED"
    db.flush()
    with pytest.raises(ValueError, match="approved review"):
        persist_approved_cycle_closing_snapshot(
            db, closing_id=closing.id, expected_state_revision=closing.state_revision,
        )


def test_snapshot_cutoff_must_match_approved_review(db):
    closing = seed(db)
    review = prepare(db, closing)
    approve(db, closing, review)
    with pytest.raises(ValueError, match="cutoff differs"):
        persist_approved_cycle_closing_snapshot(
            db, closing_id=closing.id, expected_state_revision=closing.state_revision,
            closing_cutoff_at=datetime(2027, 12, 11, 18, tzinfo=timezone.utc),
        )
    assert closing.status == "MASTER_APPROVED"


def test_review_orm_is_immutable_and_audit_rollback_is_atomic(db):
    closing = seed(db)
    review = prepare(db, closing)
    review.review_hash = "0" * 64
    with pytest.raises(RuntimeError, match="imutável"):
        db.flush()
    db.rollback()


def test_prepare_audit_failure_rolls_back_review_and_cas(db, monkeypatch):
    closing = seed(db)
    from app.services import cycle_closing_workflow as workflow

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(workflow, "_audit", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        prepare(db, closing)
    db.expire_all()
    assert db.get(type(closing), closing.id).status == "ASSESSING"
    assert db.scalar(select(sa.func.count()).select_from(CycleAnnualClosingReview)) == 0
