from datetime import date, timedelta
from decimal import Decimal

from app.services.financial_obligations import member_obligations

from test_agreement_payment_settlement_v104 import _agreement, _db, _payment, _settle


def _projected(db, member_id):
    items = member_obligations(db, member_id)
    return [item for item in items if item["obligation_type"] == "AGREEMENT_INSTALLMENT"]


def test_approved_open_agreement_installment_is_pending_obligation():
    db = _db()
    member, agreement, rows = _agreement(db)
    rows[0].due_date = date.today() + timedelta(days=10)
    db.commit()

    items = _projected(db, member.id)

    assert len(items) == 1
    assert items[0]["obligation_type"] == "AGREEMENT_INSTALLMENT"
    assert items[0]["obligation_id"] == rows[0].id
    assert items[0]["member_id"] == member.id
    assert items[0]["due_date"] == rows[0].due_date.isoformat()
    assert items[0]["amount_due"] == "100.00"
    assert items[0]["amount_paid"] == "0.00"
    assert items[0]["outstanding_amount"] == "100.00"
    assert items[0]["financial_status"] == "PENDING"


def test_approved_partially_paid_agreement_installment_is_partial():
    db = _db()
    member, agreement, rows = _agreement(db, paid="30.00", status="PARTIAL")
    rows[0].due_date = date.today() + timedelta(days=10)
    db.commit()

    items = _projected(db, member.id)

    assert len(items) == 1
    assert items[0]["amount_paid"] == "30.00"
    assert items[0]["outstanding_amount"] == "70.00"
    assert items[0]["financial_status"] == "PARTIAL"


def test_approved_pending_agreement_installment_is_overdue_after_due_date():
    db = _db()
    member, agreement, rows = _agreement(db)
    rows[0].due_date = date.today() - timedelta(days=10)
    db.commit()

    items = _projected(db, member.id)

    assert len(items) == 1
    assert items[0]["outstanding_amount"] == "100.00"
    assert items[0]["financial_status"] == "OVERDUE"
    assert items[0]["days_overdue"] == 10


def test_paid_agreement_installment_remains_a_paid_obligation():
    db = _db()
    member, agreement, rows = _agreement(db, paid="100.00", status="PAID")
    db.commit()

    items = _projected(db, member.id)

    assert len(items) == 1
    assert items[0]["amount_paid"] == "100.00"
    assert items[0]["outstanding_amount"] == "0.00"
    assert items[0]["financial_status"] == "PAID"


def test_agreement_obligation_remaining_separates_base_and_penalty_without_double_counting():
    db = _db()
    member, agreement, rows = _agreement(
        db, principal="100.00", penalty="10.00", paid="20.00", paid_penalty="3.00", status="PARTIAL"
    )
    rows[0].due_date = date.today() + timedelta(days=10)
    db.commit()

    items = _projected(db, member.id)

    assert len(items) == 1
    assert items[0]["amount_due"] == "110.00"
    assert items[0]["amount_paid"] == "23.00"
    assert items[0]["outstanding_amount"] == "87.00"
    assert items[0]["penalty_outstanding"] == "7.00"


def test_requested_and_rejected_agreements_do_not_create_active_obligations():
    db = _db()
    requested_member, requested, _ = _agreement(db)
    requested.status = "REQUESTED"
    rejected_member, rejected, _ = _agreement(db)
    rejected.status = "REJECTED"
    db.commit()

    assert _projected(db, requested_member.id) == []
    assert _projected(db, rejected_member.id) == []


def test_settled_agreement_with_paid_installment_keeps_paid_obligation_history():
    db = _db()
    member, agreement, rows = _agreement(db, paid="100.00", status="PAID")
    agreement.status = "SETTLED"
    db.commit()

    items = _projected(db, member.id)

    assert len(items) == 1
    assert items[0]["financial_status"] == "PAID"


def test_agreement_installments_keep_distinct_payment_and_receipt_evidence():
    db = _db()
    member, agreement, rows = _agreement(db, installments=2)
    rows[1].principal = Decimal("50.00")
    rows[1].amount = Decimal("50.00")
    db.flush()

    first_payment = _payment(db, member, rows[0], amount="100.00", suffix="first")
    second_payment = _payment(db, member, rows[1], amount="50.00", suffix="second")
    _settle(db, first_payment)
    _settle(db, second_payment)

    items = {item["obligation_id"]: item for item in _projected(db, member.id)}

    assert items[rows[0].id]["payment_id"] == first_payment.id
    assert items[rows[0].id]["receipt_available"] is True
    assert items[rows[1].id]["payment_id"] == second_payment.id
    assert items[rows[1].id]["receipt_available"] is True
