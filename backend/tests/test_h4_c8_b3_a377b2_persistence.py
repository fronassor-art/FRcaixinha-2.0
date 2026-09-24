"""R2 annual closing persistence, external gains and immutable evidence tests."""
from datetime import date, datetime, timezone
from decimal import Decimal
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    AgreementInstallment, CollectionAgreement, Cycle, CycleAnnualClosing,
    CycleAnnualClosingSnapshot, CycleParticipation, CycleRealizedGainEvent, Group,
    Loan, LoanInstallment, Member, Contribution, Payment, PaymentSettlement, User,
)
from app.services.cycle_closing import preview_cycle_closing

_MIGRATION_PATH = Path(__file__).parents[1] / "alembic/versions/0098_cycle_closing_persistence_a377b2.py"
_SPEC = importlib.util.spec_from_file_location("a377b2_migration", _MIGRATION_PATH)
MIGRATION = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(MIGRATION)
from app.services.cycle_closing_persistence import (
    SNAPSHOT_VERSION, canonical_snapshot_payload, create_or_get_cycle_annual_closing,
    persist_approved_cycle_closing_snapshot, record_realized_gain_event,
    reverse_realized_gain_event, snapshot_payload_hash, verify_cycle_annual_closing_snapshot,
)
from app.services.cycle_closing_workflow import (
    prepare_cycle_closing_review, approve_cycle_closing_review,
)
from app.services.ledger import post_entry

D = Decimal
CUTOFF = datetime(2027, 12, 10, 18, tzinfo=timezone.utc)
BEFORE = datetime(2027, 12, 9, 18, tzinfo=timezone.utc)
AFTER = datetime(2027, 12, 11, 18, tzinfo=timezone.utc)
HASH = hashlib.sha256(b"document evidence").hexdigest()


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "workflow_evidence_storage_root", str(tmp_path))
    engine = sa.create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        MIGRATION._create_sqlite_guards(connection)
    with Session(engine, expire_on_commit=False) as session:
        yield session
        session.rollback()
    engine.dispose()


def seed_cycle(db, *, cycle_id=1, member_id=1, participation_id=1):
    db.add(User(id=member_id, name=f"User {member_id}", email=f"u{member_id}@example.test",
                cpf=f"0000000000{member_id:04d}", password_hash="unused"))
    db.add(Group(id=member_id, name=f"Group {member_id}"))
    db.flush()


    db.add(Member(id=member_id, user_id=member_id, group_id=member_id))
    db.add(Cycle(id=cycle_id, start_date=date(2026, 12, 10), entry_deadline=date(2027, 1, 10),
                 closing_reference_date=date(2027, 12, 10), monthly_amount=D("150.00"),
                 months=12, max_quotas=50, status="OPEN"))
    db.flush()
    db.add(CycleParticipation(id=participation_id, cycle_id=cycle_id, member_id=member_id,
                              status="ACTIVE"))
    db.flush()
    db.add(Contribution(id=member_id, member_id=member_id, cycle_id=cycle_id,
                        competence=date(2027, 1, 1), amount=D("100.00"), status="PAID",
                        paid_amount=D("100.00"), paid_at=BEFORE))
    db.flush()


def approve_for_snapshot(db, closing):
    master = db.get(User, 1)
    master.role = "ADMIN"
    master.is_master = True
    db.flush()
    post_entry(db, "CAIXINHA", "CREDIT", D("100.00"), "TEST_CASH", str(closing.id))
    db.flush()
    import hashlib
    from app.models import (OperationalWorkflowTask, OperationalWorkflowOrchestration,
                            WorkflowExecutionEvidence, WorkflowExecutionEvidenceFile)
    from app.services.workflow_evidence_storage_v068 import _storage_path
    task = OperationalWorkflowTask(action_code="CLOSING_EVIDENCE", status="OPEN", priority="MEDIUM", created_by=1)
    db.add(task)
    db.flush()
    db.add(OperationalWorkflowOrchestration(task_id=task.id, priority="MEDIUM", sla_status="ON_TRACK",
                                            execution_state="IN_EXECUTION", started_by=1))
    evidence = WorkflowExecutionEvidence(task_id=task.id, added_by=1, evidence_type="ATTACHMENT",
        title="Cash position", content="stored", content_hash=hashlib.sha256(b"stored").hexdigest())
    db.add(evidence)
    db.flush()
    key = f"closing-cash-{closing.id}.txt"
    payload = b"cash statement"
    path = _storage_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    stored_file = WorkflowExecutionEvidenceFile(evidence_id=evidence.id, version=1,
        original_name="cash.txt", storage_key=key, content_type="text/plain", size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(), uploaded_by=1)
    db.add(stored_file)
    db.flush()
    from app.services.cycle_closing_workflow import record_cycle_annual_closing_cash_evidence
    cash_evidence = record_cycle_annual_closing_cash_evidence(db, closing_id=closing.id,
        file_id=stored_file.id, declared_cash_balance=D("100.00"), observed_at=CUTOFF,
        closing_cutoff_at=CUTOFF, attested_by=1)
    review = prepare_cycle_closing_review(
        db, closing_id=closing.id, expected_state_revision=closing.state_revision,
        closing_cutoff_at=CUTOFF, cash_evidence_id=cash_evidence.id, actor_id=1,
    )
    approve_cycle_closing_review(
        db, closing_id=closing.id, review_id=review.id,
        expected_state_revision=closing.state_revision, actor_id=1,
    )


def gain(db, *, event_type="INVESTMENT_YIELD_REALIZED", amount=D("20.00"),
         realized_at=BEFORE, key="gain-1", source_id="statement-row-1"):
    return record_realized_gain_event(
        db, cycle_id=1, event_type=event_type, amount=amount, realized_at=realized_at,
        source_type="BANK_STATEMENT", source_id=source_id, idempotency_key=key,
        evidence_reference="bank/2027/12/statement.pdf#row=1", evidence_hash=HASH,
    )


def settlement_evidence(db):
    contribution = db.get(Contribution, 1)
    payment = Payment(
        provider="test", provider_payment_id="settlement-proof-1",
        idempotency_key="settlement-proof-payment-1", amount=D("100.00"),
        status="approved", reference_type="CONTRIBUTION",
        reference_id=str(contribution.id), confirmed_at=BEFORE,
    )
    db.add(payment)
    db.flush()
    settlement = PaymentSettlement(
        payment_id=payment.id, member_id=contribution.member_id,
        obligation_type="CONTRIBUTION", contribution_id=contribution.id,
        amount_received=D("100.00"), amount_applied=D("100.00"),
        principal_applied=D("100.00"), interest_applied=D("0.00"),
        penalty_applied=D("0.00"), excess_amount=D("0.00"),
        obligation_status_before="PENDING", obligation_status_after="PAID",
        confirmed_at=BEFORE, confirmation_source="TEST",
        receipt_number="settlement-proof-receipt-1", receipt_version="v1",
        receipt_snapshot_json='{"settlement":1}', receipt_hash=HASH,
    )
    db.add(settlement)
    db.flush()
    return payment, settlement


def test_loan_cycle_fk_nullable_valid_and_member_cycle_mismatch_rejected(db):
    seed_cycle(db)
    db.add(Loan(id=1, member_id=1, cycle_id=None, principal=D("100.00"),
                monthly_rate=D("0.2"), installments=1, status="ACTIVE"))
    db.flush()
    db.add(Loan(id=2, member_id=1, cycle_id=1, principal=D("100.00"),
                monthly_rate=D("0.2"), installments=1, status="ACTIVE"))
    db.flush()
    db.add(Cycle(id=2, start_date=date(2028, 12, 10), entry_deadline=date(2029, 1, 10),
                 closing_reference_date=date(2029, 12, 10), monthly_amount=D("150.00"),
                 months=12, max_quotas=50, status="OPEN"))
    db.flush()
    db.add(Loan(id=3, member_id=1, cycle_id=2, principal=D("100.00"),
                monthly_rate=D("0.2"), installments=1, status="ACTIVE"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_closing_process_unique_per_cycle_and_only_supported_states(db):
    seed_cycle(db)
    closing = create_or_get_cycle_annual_closing(db, cycle_id=1)
    assert create_or_get_cycle_annual_closing(db, cycle_id=1).id == closing.id
    db.add(CycleAnnualClosing(cycle_id=1, status="NOT_A_STATE", state_revision=0))
    with pytest.raises(IntegrityError):
        db.flush()


def test_external_gain_types_money_and_idempotency_constraints(db):
    seed_cycle(db)
    event_row = gain(db)
    assert event_row.amount == D("20.00")
    with pytest.raises(ValueError, match="unsupported"):
        gain(db, event_type="LOAN_INTEREST_RECEIVED", key="bad-type")
    with pytest.raises(ValueError, match="internal source"):
        record_realized_gain_event(
            db, cycle_id=1, event_type="OTHER_REALIZED_GAIN", amount=D("2.00"),
            realized_at=BEFORE, source_type="PAYMENT_SETTLEMENT", source_id="settlement-1",
            idempotency_key="bad-source", evidence_reference="receipt/1", evidence_hash=HASH,
        )
    with pytest.raises(ValueError, match="positive"):
        gain(db, amount=D("0.00"), key="zero")
    with pytest.raises(ValueError, match="positive"):
        gain(db, amount=D("-1.00"), key="negative")
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            gain(db, key="gain-1", source_id="different-source")
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            gain(db, key="different-key", source_id="statement-row-1")


@pytest.mark.parametrize("source_type", [
    "LOAN_INTEREST", "NORMAL_PRICE_INTEREST", "FIXED_PENALTY", "LATE_INTEREST",
    "LOAN_PRINCIPAL", "CONTRIBUTION", "PAYMENT", "SETTLEMENT",
    "PAYMENT_SETTLEMENT", "UNKNOWN_EXTERNAL",
    "BANK_STATEMENT_FAKE", "BANK_CORRECTION_FAKE",
])
def test_external_gain_source_policy_is_positive_and_fail_closed(db, source_type):
    seed_cycle(db)
    with pytest.raises(ValueError, match="unsupported or belongs to an internal source"):
        record_realized_gain_event(
            db, cycle_id=1, event_type="OTHER_REALIZED_GAIN", amount=D("2.00"),
            realized_at=BEFORE, source_type=source_type, source_id="opaque-source",
            idempotency_key=f"source-policy-{source_type}",
            evidence_reference="document/row-1", evidence_hash=HASH,
        )


@pytest.mark.parametrize("source_type", ["BANK_STATEMENT", "BANK_CORRECTION"])
def test_external_gain_accepts_only_literal_source_type_and_preserves_it(db, source_type):
    seed_cycle(db)
    row = record_realized_gain_event(
        db, cycle_id=1, event_type="OTHER_REALIZED_GAIN", amount=D("2.00"),
        realized_at=BEFORE, source_type=source_type,
        source_id=f"literal-{source_type}", idempotency_key=f"literal-{source_type}",
        evidence_reference="document/row-1", evidence_hash=HASH,
    )
    assert row.source_type == source_type


@pytest.mark.parametrize("source_type", [
    " BANK_STATEMENT", "BANK_STATEMENT ", " BANK_STATEMENT ",
    "\tBANK_STATEMENT", "BANK_STATEMENT\n",
    " BANK_CORRECTION", "BANK_CORRECTION ", " BANK_CORRECTION ",
    "\tBANK_CORRECTION", "BANK_CORRECTION\n",
    "bank_statement", "bank_correction", "BANK_STATEMENT_FAKE",
])
def test_external_gain_rejects_nonliteral_source_type(db, source_type):
    seed_cycle(db)
    with pytest.raises(ValueError, match="unsupported or belongs to an internal source"):
        record_realized_gain_event(
            db, cycle_id=1, event_type="OTHER_REALIZED_GAIN", amount=D("2.00"),
            realized_at=BEFORE, source_type=source_type,
            source_id="nonliteral-source", idempotency_key="nonliteral-source",
            evidence_reference="document/row-1", evidence_hash=HASH,
        )


@pytest.mark.parametrize("collision_kind", [
    "settlement_id", "payment_id", "payment_idempotency", "receipt_hash", "receipt_reference",
])
def test_external_gain_cannot_reuse_settlement_authoritative_evidence(db, collision_kind):
    seed_cycle(db)
    payment, settlement = settlement_evidence(db)
    if collision_kind == "settlement_id":
        source_id, evidence_reference, evidence_hash = (
            f"settlement:{settlement.id}:CONTRIBUTION:original", "bank/other-row", "b" * 64,
        )
    elif collision_kind == "payment_id":
        source_id, evidence_reference, evidence_hash = (
            f"payment:{payment.id}", "bank/other-row", "b" * 64,
        )
    elif collision_kind == "payment_idempotency":
        source_id, evidence_reference, evidence_hash = (
            "statement-row-new", "bank/other-row", "b" * 64,
        )
    elif collision_kind == "receipt_hash":
        source_id, evidence_reference, evidence_hash = (
            "statement-row-new", "bank/other-row", settlement.receipt_hash,
        )
    else:
        source_id, evidence_reference, evidence_hash = (
            "statement-row-new", settlement.receipt_number, "b" * 64,
        )
    with pytest.raises(ValueError, match="matches an existing payment settlement"):
        record_realized_gain_event(
            db, cycle_id=1, event_type="OTHER_REALIZED_GAIN", amount=D("2.00"),
            realized_at=BEFORE, source_type="BANK_STATEMENT", source_id=source_id,
            idempotency_key=(payment.idempotency_key if collision_kind == "payment_idempotency"
                             else f"collision-{collision_kind}"),
            evidence_reference=evidence_reference, evidence_hash=evidence_hash,
        )
    assert db.scalar(select(sa.func.count()).select_from(CycleRealizedGainEvent)) == 0


def test_external_gain_reversal_also_reconciles_settlement_evidence(db):
    seed_cycle(db)
    original = gain(db)
    payment, _settlement = settlement_evidence(db)
    with pytest.raises(ValueError, match="matches an existing payment settlement"):
        reverse_realized_gain_event(
            db, original_event_id=original.id, realized_at=CUTOFF,
            source_type="BANK_CORRECTION", source_id=f"payment:{payment.id}",
            idempotency_key="reversal-payment-collision",
            evidence_reference="bank/reversal-doc", evidence_hash="a" * 64,
        )
    assert db.scalar(select(sa.func.count()).select_from(CycleRealizedGainEvent)) == 1


def test_settlement_reference_matching_is_exact_not_substring(db):
    seed_cycle(db)
    _payment, settlement = settlement_evidence(db)
    row = record_realized_gain_event(
        db, cycle_id=1, event_type="OTHER_REALIZED_GAIN", amount=D("2.00"),
        realized_at=BEFORE, source_type="BANK_STATEMENT",
        source_id=f"new-bank-row-{settlement.id}", idempotency_key="not-a-settlement-id",
        evidence_reference="bank/statement/new-row", evidence_hash="e" * 64,
    )
    assert row.id is not None


@pytest.mark.parametrize("source_type", [
    "PAYMENT", "SETTLEMENT", "LOAN_INTEREST", "ARBITRARY", "BANK_STATEMENT_FAKE",
])
def test_model_database_allowlist_rejects_direct_invalid_source_type(db, source_type):
    seed_cycle(db)
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(CycleRealizedGainEvent.__table__.insert().values(
                cycle_id=1, event_type="OTHER_REALIZED_GAIN", amount=D("1.00"),
                realized_at=BEFORE, source_type=source_type, source_id=f"direct-{source_type}",
                idempotency_key=f"direct-{source_type}", evidence_reference="direct-test",
                evidence_hash="e" * 64,
            ))


def test_external_gain_legitimate_evidence_is_counted_once(db):
    seed_cycle(db)
    gain(db)
    first = preview_cycle_closing(db, cycle_id=1, closing_cutoff_at=CUTOFF)
    assert first["gross_realized_result"] == "20.00"
    assert len(first["source_trace"]["included_gains"]) == 1
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            gain(db, key="gain-1", source_id="another-statement-row")


def test_settlement_loan_interest_cannot_also_be_recorded_as_external_gain(db):
    seed_cycle(db)
    loan = Loan(
        member_id=1, cycle_id=1, principal=D("100.00"), monthly_rate=D("0.20"),
        installments=1, status="ACTIVE",
    )
    db.add(loan)
    db.flush()
    installment = LoanInstallment(
        loan_id=loan.id, number=1, due_date=date(2027, 11, 1),
        principal=D("100.00"), interest=D("10.00"), amount=D("110.00"),
        paid_amount=D("110.00"), penalty_amount=D("0.00"),
        paid_penalty_amount=D("0.00"), status="PAID",
    )
    db.add(installment)
    db.flush()
    payment = Payment(
        provider="test", provider_payment_id="loan-interest-payment-1",
        idempotency_key="loan-interest-payment-idem-1", amount=D("110.00"),
        status="approved", reference_type="LOAN_INSTALLMENT",
        reference_id=str(installment.id), confirmed_at=BEFORE,
    )
    db.add(payment)
    db.flush()
    settlement = PaymentSettlement(
        payment_id=payment.id, member_id=1, obligation_type="LOAN_INSTALLMENT",
        loan_installment_id=installment.id, amount_received=D("110.00"),
        amount_applied=D("110.00"), principal_applied=D("100.00"),
        interest_applied=D("10.00"), penalty_applied=D("0.00"),
        excess_amount=D("0.00"), obligation_status_before="OPEN",
        obligation_status_after="PAID", confirmed_at=BEFORE,
        confirmation_source="TEST", receipt_number="loan-interest-receipt-1",
        receipt_version="v1", receipt_snapshot_json="{}", receipt_hash="f" * 64,
    )
    db.add(settlement)
    db.flush()

    with pytest.raises(ValueError, match="matches an existing payment settlement"):
        record_realized_gain_event(
            db, cycle_id=1, event_type="OTHER_REALIZED_GAIN", amount=D("10.00"),
            realized_at=BEFORE, source_type="BANK_STATEMENT",
            source_id=f"settlement:{settlement.id}:LOAN_INTEREST:original",
            idempotency_key="loan-interest-double-count-attempt",
            evidence_reference="bank/statement/reused-loan-receipt",
            evidence_hash="d" * 64,
        )
    result = preview_cycle_closing(db, cycle_id=1, closing_cutoff_at=CUTOFF)
    assert result["gross_realized_result"] == "10.00"
    assert [row["kind"] for row in result["source_trace"]["included_gains"]] == ["LOAN_INTEREST"]
    assert any(row["kind"] == "LOAN_PRINCIPAL"
               for row in result["source_trace"]["excluded_gains"])


def test_gain_original_is_immutable_and_full_reversal_is_compensatory(db):
    seed_cycle(db)
    original = gain(db)
    reversal = reverse_realized_gain_event(
        db, original_event_id=original.id, realized_at=CUTOFF,
        source_type="BANK_CORRECTION", source_id="statement-reversal-1",
        idempotency_key="gain-reversal-1", evidence_reference="bank/reversal.pdf#row=1",
        evidence_hash=HASH,
    )
    assert reversal.amount == original.amount == D("20.00")
    assert reversal.reversal_of_id == original.id
    assert db.get(CycleRealizedGainEvent, original.id).reversal_of_id is None
    with pytest.raises(ValueError, match="only one full reversal"):
        reverse_realized_gain_event(
            db, original_event_id=original.id, realized_at=AFTER,
            source_type="BANK_CORRECTION", source_id="another-reversal",
            idempotency_key="gain-reversal-2", evidence_reference="bank/reversal2.pdf",
            evidence_hash=HASH,
        )
    with pytest.raises(RuntimeError, match="imutável"):
        original.amount = D("21.00")
        db.flush()


def test_gain_db_triggers_block_raw_update_and_delete(db):
    seed_cycle(db)
    row = gain(db)
    with pytest.raises(sa.exc.DatabaseError):
        with db.begin_nested():
            db.execute(text("UPDATE cycle_realized_gain_events SET amount=21 WHERE id=:id"), {"id": row.id})
    with pytest.raises(sa.exc.DatabaseError):
        with db.begin_nested():
            db.execute(text("DELETE FROM cycle_realized_gain_events WHERE id=:id"), {"id": row.id})


def test_external_gains_enter_result_and_full_reversal_obeys_inclusive_cutoff(db):
    seed_cycle(db)
    original = gain(db, realized_at=BEFORE)
    reverse_realized_gain_event(
        db, original_event_id=original.id, realized_at=CUTOFF,
        source_type="BANK_CORRECTION", source_id="statement-reversal-1",
        idempotency_key="gain-reversal-1", evidence_reference="bank/reversal.pdf#row=1",
        evidence_hash=HASH,
    )
    before_reversal = preview_cycle_closing(db, cycle_id=1, closing_cutoff_at=BEFORE)
    at_reversal = preview_cycle_closing(db, cycle_id=1, closing_cutoff_at=CUTOFF)
    after = preview_cycle_closing(db, cycle_id=1, closing_cutoff_at=AFTER)
    assert before_reversal["gross_realized_result"] == "20.00"
    assert at_reversal["gross_realized_result"] == "0.00"
    assert after["gross_realized_result"] == "0.00"


def test_external_gain_after_cutoff_is_not_included(db):
    seed_cycle(db)
    gain(db, realized_at=AFTER)
    result = preview_cycle_closing(db, cycle_id=1, closing_cutoff_at=CUTOFF)
    assert result["gross_realized_result"] == "0.00"
    assert result["source_trace"]["excluded_gains"][0]["reason"] == "OUTSIDE_CYCLE_CUTOFF"


def test_snapshot_is_canonical_hashable_unique_and_immutable(db):
    seed_cycle(db)
    closing = create_or_get_cycle_annual_closing(db, cycle_id=1)
    approve_for_snapshot(db, closing)
    snapshot = persist_approved_cycle_closing_snapshot(
        db, closing_id=closing.id, closing_cutoff_at=CUTOFF, closed_by=None,
        expected_state_revision=closing.state_revision,
    )
    assert snapshot.snapshot_version == SNAPSHOT_VERSION
    assert snapshot.payload_hash == snapshot_payload_hash(snapshot.canonical_payload)
    assert verify_cycle_annual_closing_snapshot(snapshot)
    assert closing.status == "CLOSED"
    result = preview_cycle_closing(db, cycle_id=1, closing_cutoff_at=CUTOFF)
    canonical = canonical_snapshot_payload(result)
    assert canonical == canonical_snapshot_payload(
        preview_cycle_closing(db, cycle_id=1, closing_cutoff_at=CUTOFF)
    )
    with pytest.raises(ValueError, match="result schema"):
        canonical_snapshot_payload({"z": D("1.00"), "a": [D("2.00")]})
    unknown_version = deepcopy(result)
    unknown_version["calculation_version"] = "cycle_closing_v999"
    with pytest.raises(ValueError, match="unsupported closing calculation version"):
        canonical_snapshot_payload(unknown_version)
    missing_total = deepcopy(result)
    del missing_total["gross_realized_result"]
    with pytest.raises(ValueError, match="result schema"):
        canonical_snapshot_payload(missing_total)
    with pytest.raises(ValueError, match="Decimal string"):
        _canonical_test_float(result)
    with pytest.raises(RuntimeError, match="imutável"):
        snapshot.payload_hash = "0" * 64
        db.flush()


def _canonical_test_float(result):
    altered = deepcopy(result)
    altered["gross_realized_result"] = 0.1
    return canonical_snapshot_payload(altered)


def test_valid_financial_memory_change_changes_canonical_hash(db):
    seed_cycle(db)
    gain(db, amount=D("20.00"), key="memory-1", source_id="statement-row-1")
    first = preview_cycle_closing(db, cycle_id=1, closing_cutoff_at=CUTOFF)
    first_hash = snapshot_payload_hash(canonical_snapshot_payload(first))
    gain(db, amount=D("1.00"), key="memory-2", source_id="statement-row-2")
    second = preview_cycle_closing(db, cycle_id=1, closing_cutoff_at=CUTOFF)
    assert second["gross_realized_result"] == "21.00"
    assert snapshot_payload_hash(canonical_snapshot_payload(second)) != first_hash


def test_snapshot_db_trigger_and_one_per_cycle(db):
    seed_cycle(db)
    closing = create_or_get_cycle_annual_closing(db, cycle_id=1)
    approve_for_snapshot(db, closing)
    snapshot = persist_approved_cycle_closing_snapshot(db, closing_id=closing.id,
                                                        closing_cutoff_at=CUTOFF,
                                                        expected_state_revision=closing.state_revision)
    with pytest.raises(sa.exc.DatabaseError):
        with db.begin_nested():
            db.execute(text("UPDATE cycle_annual_closing_snapshots SET payload_hash=:hash WHERE id=:id"),
                       {"hash": "0" * 64, "id": snapshot.id})
    with pytest.raises(sa.exc.DatabaseError):
        with db.begin_nested():
            db.execute(text("DELETE FROM cycle_annual_closing_snapshots WHERE id=:id"), {"id": snapshot.id})


def test_snapshot_persistence_requires_approved_closing_and_keeps_preview_read_only(db):
    seed_cycle(db)
    closing = create_or_get_cycle_annual_closing(db, cycle_id=1)
    with pytest.raises(ValueError, match="approved by Master"):
        persist_approved_cycle_closing_snapshot(db, closing_id=closing.id,
                                                closing_cutoff_at=CUTOFF)
    before_rows = db.scalar(select(sa.func.count()).select_from(CycleAnnualClosingSnapshot))
    before_preview = preview_cycle_closing(db, cycle_id=1, closing_cutoff_at=CUTOFF)
    assert before_rows == 0
    assert before_preview["gross_realized_result"] == "0.00"


def test_agreement_receipt_inherits_cycle_only_from_linked_loan(db):
    seed_cycle(db)
    loan = Loan(member_id=1, cycle_id=1, principal=D("100.00"), monthly_rate=D("0.2"),
                installments=1, status="RESTRUCTURED")
    db.add(loan)
    db.flush()
    old_installment = LoanInstallment(loan_id=loan.id, number=1, due_date=date(2027, 11, 10),
        principal=D("100.00"), interest=D("0.00"), amount=D("100.00"),
        paid_amount=D("0.00"), penalty_amount=D("0.00"), status="AGREED")
    agreement = CollectionAgreement(loan_id=loan.id, member_id=1, requested_by=1,
        status="APPROVED", installments=1, total_amount=D("100.00"), snapshot="{}",
        decided_at=BEFORE)
    db.add_all([old_installment, agreement])
    db.flush()
    installment = AgreementInstallment(agreement_id=agreement.id, number=1,
        due_date=date(2028, 1, 10), principal=D("95.00"), penalty_amount=D("5.00"),
        amount=D("100.00"), paid_amount=D("0.00"), paid_penalty_amount=D("0.00"),
        status="OPEN")
    db.add(installment)
    db.flush()
    payment = Payment(provider="test", provider_payment_id="agreement-cycle-gain",
        idempotency_key="agreement-cycle-gain", amount=D("5.00"), status="approved",
        reference_type="AGREEMENT_INSTALLMENT", reference_id=str(installment.id), confirmed_at=BEFORE)
    db.add(payment)
    db.flush()
    db.add(PaymentSettlement(payment_id=payment.id, member_id=1,
        obligation_type="AGREEMENT_INSTALLMENT", agreement_installment_id=installment.id,
        amount_received=D("5.00"), amount_applied=D("5.00"), principal_applied=D("0.00"),
        interest_applied=D("0.00"), penalty_applied=D("5.00"), excess_amount=D("0.00"),
        obligation_status_before="OPEN", obligation_status_after="PARTIAL",
        confirmed_at=BEFORE, confirmation_source="TEST", receipt_number="agreement-cycle-receipt",
        receipt_version="v1", receipt_snapshot_json="{}", receipt_hash="agreement-cycle-hash"))
    db.commit()
    result = preview_cycle_closing(db, cycle_id=1, closing_cutoff_at=CUTOFF)
    assert result["gross_realized_result"] == "5.00"
    included = result["source_trace"]["included_gains"]
    assert [(item["kind"], item["source_type"]) for item in included] == [("AGREEMENT_PENALTY", "PAYMENT_SETTLEMENT")]
    assert not any(item["kind"] == "AGREEMENT_PRINCIPAL" for item in included)
