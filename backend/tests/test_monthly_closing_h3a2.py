from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import text

from app.services.monthly_closing_v040 import close_month_v040
from app.services.payment_financial_events import payment_financial_events
from app.services.payment_reversal import reverse_payment
from app.services.reconciliation_v040 import build_advanced_reconciliation

from test_payment_reversal_contribution_v104 import _db, _setup
from test_payment_settlement_v103 import _installment, _member, _payment, _settle


def _utc(year, month, day, hour=12):
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _snapshot(db, year, month):
    return build_advanced_reconciliation(db, date(year, month, 1))["snapshot"]


def test_contribution_h2b_values_are_temporal_across_months_and_same_month():
    db = _db()
    admin, _contribution, payment, settlement = _setup(db, suffix="h3a2-contribution-cross")
    settlement.confirmed_at = _utc(2026, 12, 20)
    db.commit()
    reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno temporal", now=_utc(2027, 1, 10))
    db.commit()

    assert _snapshot(db, 2026, 12)["contributions_paid"] == "100.00"
    assert _snapshot(db, 2027, 1)["contributions_paid"] == "-100.00"

    db2 = _db()
    admin2, _contribution2, payment2, settlement2 = _setup(db2, suffix="h3a2-contribution-same")
    settlement2.confirmed_at = _utc(2027, 2, 10)
    db2.commit()
    reverse_payment(db2, payment_id=payment2.id, admin_id=admin2.id, reason="Estorno mesmo mes", now=_utc(2027, 2, 11))
    db2.commit()
    assert _snapshot(db2, 2027, 2)["contributions_paid"] == "0.00"


def test_invalid_contribution_reversal_does_not_create_negative_event():
    db = _db()
    admin, _contribution, payment, settlement = _setup(db, suffix="h3a2-contribution-invalid")
    settlement.confirmed_at = _utc(2027, 3, 10)
    db.commit()
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno invalido", now=_utc(2027, 3, 11))
    db.execute(text("UPDATE payment_reversals SET receipt_hash = 'invalid' WHERE id = :id"), {"id": reversal.id})
    db.commit()

    assert _snapshot(db, 2027, 3)["contributions_paid"] == "100.00"


def test_interest_h2b_values_are_temporal_and_zero_interest_is_absent():
    db = _db()
    admin, _contribution, _contribution_payment, _contribution_settlement = _setup(db, suffix="h3a2-interest-cross")
    member = _member(db, "h3a2-interest-cross-loan")
    _loan, installment = _installment(db, member, amount="120.00", interest="20.00", penalty="0.00")
    payment = _payment(db, suffix="h3a2-interest-cross-loan", amount="120.00", reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id))
    _settle(db, payment, when=_utc(2026, 12, 20))
    reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno juros", now=_utc(2027, 1, 10))
    db.commit()

    assert _snapshot(db, 2026, 12)["interest_received"] == "20.00"
    assert _snapshot(db, 2027, 1)["interest_received"] == "-20.00"

    db2 = _db()
    _admin2, _contribution2, _payment2, _settlement2 = _setup(db2, suffix="h3a2-interest-zero")
    member2 = _member(db2, "h3a2-interest-zero-loan")
    _loan2, installment2 = _installment(db2, member2, amount="100.00", interest="0.00", penalty="0.00")
    payment2 = _payment(db2, suffix="h3a2-interest-zero-loan", amount="100.00", reference_type="LOAN_INSTALLMENT", reference_id=str(installment2.id))
    _settle(db2, payment2, when=_utc(2027, 2, 10))
    assert _snapshot(db2, 2027, 2)["interest_received"] == "0.00"


def test_close_persists_reversal_aware_totals_without_changing_snapshot_shape():
    db = _db()
    admin, _contribution, _contribution_payment, contribution_settlement = _setup(db, suffix="h3a2-close")
    close_month = date.today().replace(day=1)
    contribution_settlement.confirmed_at = datetime(
        close_month.year, close_month.month, 10, 12, tzinfo=timezone.utc
    )
    member = _member(db, "h3a2-close-loan")
    _loan, installment = _installment(db, member, amount="120.00", interest="20.00", penalty="0.00")
    loan_payment = _payment(db, suffix="h3a2-close-loan", amount="120.00", reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id))
    _settle(db, loan_payment, when=datetime(
        close_month.year, close_month.month, 11, 12, tzinfo=timezone.utc
    ))
    db.commit()

    closing, snapshot, _hash = close_month_v040(db, close_month, admin.id)
    db.commit()
    db.expire(closing)

    assert snapshot["contributions_paid"] == "100.00"
    assert snapshot["interest_received"] == "20.00"
    assert closing.total_contributions == Decimal("100.00")
    assert closing.total_interest_received == Decimal("20.00")
    assert set(snapshot) == {
        "schema", "competence", "period_end", "contributions_paid", "contributions_ledger",
        "loan_payments_ledger", "agreement_payments_ledger", "interest_received",
        "loan_disbursements_ledger", "expenses_posted", "expenses_ledger", "approved_payments",
        "posted_payments", "open_loan_exposure", "open_agreement_exposure", "ledger_credits",
        "ledger_debits", "ledger_net", "findings", "closing_schema", "reconciliation_hash",
    }


def test_interest_same_month_reversal_is_zero_and_keeps_both_events():
    db = _db()
    admin, _contribution, _contribution_payment, _contribution_settlement = _setup(
        db, suffix="h3a2-interest-same"
    )
    member = _member(db, "h3a2-interest-same-loan")
    _loan, installment = _installment(db, member, amount="120.00", interest="20.00", penalty="0.00")
    payment = _payment(
        db,
        suffix="h3a2-interest-same-loan",
        amount="120.00",
        reference_type="LOAN_INSTALLMENT",
        reference_id=str(installment.id),
    )
    _settle(db, payment, when=_utc(2027, 2, 10))
    reversal = reverse_payment(
        db,
        payment_id=payment.id,
        admin_id=admin.id,
        reason="Estorno de juros no mesmo mês",
        now=_utc(2027, 2, 11),
    )
    db.commit()

    snapshot = _snapshot(db, 2027, 2)
    interest_events = [
        event
        for event in payment_financial_events(db, start=_utc(2027, 2, 1), end=_utc(2027, 3, 1))
        if event.component == "LOAN_INTEREST"
    ]
    assert [event.amount for event in interest_events] == [Decimal("20.00"), Decimal("-20.00")]
    assert snapshot["interest_received"] == "0.00"
    assert reversal.reversal_competence == date(2027, 2, 1)


def test_closed_december_is_unchanged_after_valid_january_reversal():
    db = _db()
    admin, _contribution, payment, settlement = _setup(db, suffix="h3a2-closed-december")
    december_payment_time = _utc(2026, 12, 20)
    settlement.confirmed_at = december_payment_time
    payment.created_at = december_payment_time
    payment.ledger_posted_at = december_payment_time
    db.execute(
        text("UPDATE ledger_entries SET created_at = :created_at WHERE reference_id = :payment_id"),
        {"created_at": december_payment_time, "payment_id": str(payment.id)},
    )
    db.commit()

    closing, _snapshot_value, _hash = close_month_v040(db, date(2026, 12, 1), admin.id)
    db.commit()
    db.refresh(closing)
    before = {
        "status": closing.status,
        "total_contributions": closing.total_contributions,
        "total_expenses": closing.total_expenses,
        "total_interest_received": closing.total_interest_received,
        "ledger_balance": closing.ledger_balance,
        "snapshot_json": closing.snapshot_json,
        "snapshot_hash": closing.snapshot_hash,
        "closed_by": closing.closed_by,
        "closed_at": closing.closed_at,
    }

    reverse_payment(
        db,
        payment_id=payment.id,
        admin_id=admin.id,
        reason="Estorno em janeiro após fechamento",
        now=_utc(2027, 1, 10),
    )
    db.commit()
    db.expire_all()
    reloaded = db.query(type(closing)).filter_by(competence=date(2026, 12, 1)).one()
    after = {
        "status": reloaded.status,
        "total_contributions": reloaded.total_contributions,
        "total_expenses": reloaded.total_expenses,
        "total_interest_received": reloaded.total_interest_received,
        "ledger_balance": reloaded.ledger_balance,
        "snapshot_json": reloaded.snapshot_json,
        "snapshot_hash": reloaded.snapshot_hash,
        "closed_by": reloaded.closed_by,
        "closed_at": reloaded.closed_at,
    }
    assert after == before


def test_reconciliation_uses_utc_half_open_month_boundary():
    db = _db()
    _admin_a, _contribution_a, _payment_a, settlement_a = _setup(db, suffix="h3a2-boundary-before-end")
    settlement_a.confirmed_at = datetime(2027, 1, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
    db.commit()

    january = _snapshot(db, 2027, 1)
    db2 = _db()
    _admin_b, _contribution_b, _payment_b, settlement_b = _setup(db2, suffix="h3a2-boundary-at-end")
    settlement_b.confirmed_at = datetime(2027, 2, 1, 0, 0, 0, tzinfo=timezone.utc)
    db2.commit()
    january_at_end = _snapshot(db2, 2027, 1)
    february = _snapshot(db2, 2027, 2)
    assert january_at_end["contributions_paid"] == "0.00"
    assert january["contributions_paid"] == "100.00"
    assert february["contributions_paid"] == "100.00"
