from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import event, text

from app.models import LedgerEntry, Payment, PaymentReversal, PaymentSettlement
from app.services.reports_v10 import member_statement
from app.services.payment_reversal import reverse_payment
from app.services.ledger import reverse_entry

from test_payment_reversal_contribution_v104 import _db as contribution_db, _setup as setup_contribution
from test_payment_reversal_loan_v105 import _loan_payment
from test_payment_reversal_agreement_v106 import _agreement_payment


def _rows(statement, payment_id):
    return [row for row in statement["movements"] if row["payment_id"] == payment_id]


def test_contribution_reversal_uses_reversal_competence_and_keeps_original_competence():
    db = contribution_db()
    admin, contribution, payment, _settlement = setup_contribution(db, suffix="h2a-contribution")
    reversal = reverse_payment(
        db,
        payment_id=payment.id,
        admin_id=admin.id,
        reason="Estorno H2A",
        now=datetime(2026, 3, 4, 12, tzinfo=timezone.utc),
    )
    db.commit()

    rows = _rows(member_statement(db, contribution.member_id), payment.id)
    original = next(row for row in rows if row["type"] == "CONTRIBUTION_PAYMENT")
    compensation = next(row for row in rows if row["type"] == "REVERSAL")
    assert original["competence"] == contribution.competence.isoformat()
    assert compensation["competence"] == "2026-03-01"
    reversed_at = reversal.reversed_at.replace(tzinfo=timezone.utc)
    assert compensation["occurred_at"] == reversed_at.isoformat()
    assert original["direction"] == "DEBIT"
    assert compensation["direction"] == "CREDIT"
    assert Decimal(original["total"]) - Decimal(compensation["total"]) == Decimal("0.00")


def test_generic_contribution_reversal_is_not_projected_as_payment_compensation():
    db = contribution_db()
    _admin, contribution, payment, _settlement = setup_contribution(db, suffix="h2a-generic")
    original = db.query(LedgerEntry).filter_by(reference_id=str(payment.id)).one()
    reverse_entry(db, original, "Ajuste genérico H2A")
    db.commit()

    rows = _rows(member_statement(db, contribution.member_id), payment.id)
    assert [row["type"] for row in rows] == ["CONTRIBUTION_PAYMENT"]


def test_invalid_contribution_reversal_does_not_neutralize_statement():
    db = contribution_db()
    admin, contribution, payment, _settlement = setup_contribution(db, suffix="h2a-invalid")
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno H2A")
    db.commit()
    db.execute(text("UPDATE payment_reversals SET receipt_snapshot_json = '{}' WHERE id = :id"), {"id": reversal.id})
    db.commit()

    rows = _rows(member_statement(db, contribution.member_id), payment.id)
    assert [row["type"] for row in rows] == ["CONTRIBUTION_PAYMENT"]


def test_loan_reversal_projects_principal_mfe_and_ledger_components_without_double_counting():
    db = contribution_db()
    admin, member, _loan, _installment, payment, _settlement = _loan_payment(
        db, "h2a-loan", amount="130.00", interest="20.00", penalty="10.00"
    )
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno H2A")
    db.commit()

    rows = _rows(member_statement(db, member.id), payment.id)
    original = next(row for row in rows if row["type"] == "LOAN_INSTALLMENT_PAYMENT")
    compensations = [row for row in rows if row["type"] == "REVERSAL"]
    assert original["principal"] == "100.00"
    assert original["interest"] == "20.00"
    assert original["penalty"] == "10.00"
    assert sum(Decimal(row["total"]) for row in compensations) == Decimal("130.00")
    assert sum(Decimal(row["principal"] or "0.00") for row in compensations) == Decimal("100.00")
    assert sum(Decimal(row["interest"] or "0.00") for row in compensations) == Decimal("20.00")
    assert sum(Decimal(row["penalty"] or "0.00") for row in compensations) == Decimal("10.00")
    assert all(row["direction"] == "CREDIT" for row in compensations)
    reversed_at = reversal.reversed_at.replace(tzinfo=timezone.utc)
    assert all(row["occurred_at"] == reversed_at.isoformat() for row in compensations)


def test_loan_zero_principal_reversal_projects_only_existing_ledger_component():
    db = contribution_db()
    admin, member, _loan, _installment, payment, _settlement = _loan_payment(
        db, "h2a-loan-zero", amount="20.00", interest="20.00", penalty="0.00"
    )
    reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno H2A")
    db.commit()

    rows = _rows(member_statement(db, member.id), payment.id)
    compensations = [row for row in rows if row["type"] == "REVERSAL"]
    assert len(compensations) == 1
    assert compensations[0]["interest"] == "20.00"
    assert compensations[0]["penalty"] is None
    assert compensations[0]["principal"] is None


def test_loan_penalty_only_reversal_projects_penalty_without_principal_or_interest():
    db = contribution_db()
    admin, member, _loan, _installment, payment, _settlement = _loan_payment(
        db, "h2a-loan-penalty", amount="10.00", interest="0.00", penalty="10.00"
    )
    reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno H2A")
    db.commit()

    rows = _rows(member_statement(db, member.id), payment.id)
    compensations = [row for row in rows if row["type"] == "REVERSAL"]
    assert len(compensations) == 1
    assert compensations[0]["penalty"] == "10.00"
    assert compensations[0]["interest"] is None
    assert compensations[0]["principal"] is None


def test_invalid_loan_reversal_does_not_neutralize_statement():
    db = contribution_db()
    admin, member, _loan, _installment, payment, _settlement = _loan_payment(db, "h2a-loan-invalid")
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno H2A")
    db.commit()
    db.execute(text("UPDATE payment_reversals SET receipt_hash = '0' WHERE id = :id"), {"id": reversal.id})
    db.commit()

    rows = _rows(member_statement(db, member.id), payment.id)
    assert [row["type"] for row in rows] == ["LOAN_INSTALLMENT_PAYMENT"]


def test_agreement_reversal_projects_two_opposite_events_and_no_mfe():
    db = contribution_db()
    admin, agreement, installment, payment, _settlement = _agreement_payment(
        db, "h2a-agreement", received="25.00"
    )
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno H2A")
    db.commit()

    rows = _rows(member_statement(db, agreement.member_id), payment.id)
    assert {row["type"] for row in rows} == {"REVERSAL", "AGREEMENT_INSTALLMENT_PAYMENT"}
    original = next(row for row in rows if row["type"] == "AGREEMENT_INSTALLMENT_PAYMENT")
    compensation = next(row for row in rows if row["type"] == "REVERSAL")
    assert original["direction"] == "DEBIT"
    assert compensation["direction"] == "CREDIT"
    assert original["total"] == compensation["total"] == "25.00"
    reversed_at = reversal.reversed_at.replace(tzinfo=timezone.utc)
    assert compensation["occurred_at"] == reversed_at.isoformat()
    assert installment.paid_amount == Decimal("0.00")
    assert db.query(PaymentSettlement).filter_by(payment_id=payment.id).count() == 1


def test_invalid_agreement_reversal_does_not_neutralize_statement():
    db = contribution_db()
    admin, agreement, _installment, payment, _settlement = _agreement_payment(
        db, "h2a-agreement-invalid", received="25.00"
    )
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno H2A")
    db.commit()
    db.execute(text("UPDATE payment_reversals SET receipt_snapshot_json = '{}' WHERE id = :id"), {"id": reversal.id})
    db.commit()

    rows = _rows(member_statement(db, agreement.member_id), payment.id)
    assert [row["type"] for row in rows] == ["AGREEMENT_INSTALLMENT_PAYMENT"]


def test_statement_is_deterministic_and_does_not_autoflush_dirty_state():
    db = contribution_db()
    _admin, contribution, payment, _settlement = setup_contribution(db, suffix="h2a-read-only")
    member_id = contribution.member_id
    flushes = []

    @event.listens_for(db, "before_flush")
    def _before_flush(*_args):
        flushes.append(True)

    pending = Payment(
        provider="test",
        provider_payment_id="pending-h2a",
        idempotency_key="pending-h2a",
        amount=Decimal("1.00"),
        status="approved",
    )
    db.add(pending)
    first = member_statement(db, member_id)
    second = member_statement(db, member_id)
    assert first == second
    assert flushes == []
    assert pending in db.new
    db.rollback()
