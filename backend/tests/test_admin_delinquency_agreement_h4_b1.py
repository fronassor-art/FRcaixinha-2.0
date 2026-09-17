from datetime import date, timedelta
from decimal import Decimal

from app.api.admin import delinquency, delinquency_summary
from app.models import Contribution

from test_agreement_payment_settlement_v104 import _agreement, _db


def _items(db, **filters):
    return delinquency(admin=object(), db=db, **filters)["items"]


def test_delinquency_projects_agreement_installment_with_component_values():
    db = _db()
    member, agreement, rows = _agreement(
        db,
        principal="100.00",
        penalty="10.00",
        paid="40.00",
        paid_penalty="3.00",
        status="PARTIAL",
    )
    rows[0].due_date = date.today() - timedelta(days=2)
    db.commit()

    item = _items(db)[0]

    assert item["obligation_type"] == "AGREEMENT_INSTALLMENT"
    assert item["obligation_id"] == rows[0].id
    assert item["member_id"] == member.id
    assert item["loan_id"] == agreement.loan_id
    assert item["installment_number"] == rows[0].number
    assert item["due_date"] == rows[0].due_date.isoformat()
    assert item["amount_due"] == "110.00"
    assert item["amount_paid"] == "43.00"
    assert item["outstanding_amount"] == "67.00"
    assert item["principal_outstanding"] == "60.00"
    assert item["penalty_outstanding"] == "7.00"
    assert item["interest_outstanding"] == "0.00"
    assert item["financial_status"] == "OVERDUE"


def test_delinquency_agreement_type_filter_excludes_other_obligation_types():
    db = _db()
    member, _, rows = _agreement(db)
    rows[0].due_date = date.today() - timedelta(days=1)
    db.add(
        Contribution(
            member_id=member.id,
            competence=date(2026, 9, 1),
            amount=Decimal("25.00"),
            due_date=date.today() - timedelta(days=1),
            paid_amount=Decimal("0.00"),
            status="PENDING",
        )
    )
    db.commit()

    items = _items(db, obligation_type="AGREEMENT_INSTALLMENT")

    assert len(items) == 1
    assert items[0]["obligation_type"] == "AGREEMENT_INSTALLMENT"


def test_delinquency_overdue_only_keeps_only_overdue_agreements():
    db = _db()
    overdue_member, _, overdue_rows = _agreement(db)
    future_member, _, future_rows = _agreement(db)
    overdue_rows[0].due_date = date.today() - timedelta(days=1)
    future_rows[0].due_date = date.today() + timedelta(days=1)
    db.commit()

    all_items = _items(db)
    overdue_items = _items(db, overdue_only=True)

    assert len(all_items) == 2
    assert {item["member_id"] for item in overdue_items} == {overdue_member.id}
    assert all(item["financial_status"] == "OVERDUE" for item in overdue_items)
    assert future_member.id not in {item["member_id"] for item in overdue_items}


def test_settled_paid_agreement_is_not_an_admin_delinquency_item():
    db = _db()
    member, agreement, rows = _agreement(db, principal="100.00", paid="100.00", status="PAID")
    agreement.status = "SETTLED"
    db.commit()

    assert _items(db) == []


def test_delinquency_summary_includes_agreement_components_and_distinct_member():
    db = _db()
    _, _, rows = _agreement(
        db,
        principal="100.00",
        penalty="10.00",
        paid="40.00",
        paid_penalty="3.00",
        status="PARTIAL",
    )
    rows[0].due_date = date.today() - timedelta(days=1)
    db.commit()

    summary = delinquency_summary(admin=object(), db=db)

    assert summary["total_outstanding"] == "67.00"
    assert summary["total_penalty_outstanding"] == "7.00"
    assert summary["total_interest_outstanding"] == "0.00"
    assert summary["delinquent_members_count"] == 1


def test_delinquency_summary_by_type_counts_agreement_installments():
    db = _db()
    _, _, rows = _agreement(db)
    rows[0].due_date = date.today() - timedelta(days=1)
    db.commit()

    summary = delinquency_summary(admin=object(), db=db)

    assert summary["by_type"]["agreements"] == 1


def test_restored_approved_agreement_current_state_reappears_in_delinquency():
    db = _db()
    member, agreement, rows = _agreement(db, principal="100.00", paid="100.00", status="PAID")
    agreement.status = "SETTLED"
    db.commit()
    assert _items(db) == []

    agreement.status = "APPROVED"
    rows[0].status = "PARTIAL"
    rows[0].paid_amount = Decimal("40.00")
    rows[0].paid_penalty_amount = Decimal("0.00")
    rows[0].due_date = date.today() - timedelta(days=1)
    db.commit()

    items = _items(db)

    assert len(items) == 1
    assert items[0]["member_id"] == member.id
    assert items[0]["financial_status"] == "OVERDUE"
    assert items[0]["outstanding_amount"] == "60.00"
