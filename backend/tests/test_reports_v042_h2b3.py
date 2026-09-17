from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import text

from app.models import LedgerEntry, MemberFinancialEntry, MonthlyClosing
from app.services.ledger import reverse_entry
from app.services.payment_reversal import reverse_payment
from app.services.reports_v042 import annual_accountability, monthly_accountability

from test_agreement_payment_settlement_v104 import _agreement, _payment as agreement_payment, _settle as agreement_settle
from test_payment_reversal_contribution_v104 import _db, _setup
from test_payment_settlement_v103 import _installment, _member, _payment, _settle


def _utc(year, month, day, hour=12):
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _sum_inflow(report, key):
    return sum((Decimal(row["inflows"][key]) for row in report["months"]), Decimal("0.00"))


def _sum_monthly(report, key):
    return sum((Decimal(row[key]) for row in report["months"]), Decimal("0.00"))


def test_annual_shape_has_twelve_ordered_months_and_exact_monthly_totals():
    db = _db()
    _admin, _contribution, _payment_row, settlement = _setup(db, suffix="h2b3-shape")
    settlement.confirmed_at = _utc(2027, 4, 1)
    db.commit()

    report = annual_accountability(db, 2027)

    assert set(report) == {"schema", "report_type", "year", "months", "totals"}
    assert report["schema"] == "v0.42"
    assert report["report_type"] == "ANNUAL"
    assert report["year"] == 2027
    assert len(report["months"]) == 12
    assert [row["competence"] for row in report["months"]] == [f"2027-{month:02d}-01" for month in range(1, 13)]
    assert set(report["totals"]) == {
        "contributions_paid", "interest_received", "penalties_received", "expenses", "operating_result"
    }
    assert report["totals"]["contributions_paid"] == "100.00"
    assert report["totals"]["interest_received"] == "0.00"
    assert report["totals"]["penalties_received"] == "0.00"
    assert report["totals"]["expenses"] == "0.00"
    assert report["totals"]["operating_result"] == "100.00"
    assert Decimal(report["totals"]["contributions_paid"]) == _sum_inflow(report, "contributions_paid")
    assert Decimal(report["totals"]["interest_received"]) == _sum_inflow(report, "interest_received")
    assert Decimal(report["totals"]["penalties_received"]) == _sum_inflow(report, "penalties_received")
    assert Decimal(report["totals"]["expenses"]) == _sum_monthly(report, "expenses")
    assert Decimal(report["totals"]["operating_result"]) == _sum_monthly(report, "operating_result")


def test_annual_totals_are_exact_sums_of_months_and_exclude_principal_and_agreement():
    db = _db()
    admin, _contribution, contribution_payment, contribution_settlement = _setup(db, suffix="h2b3-components")
    contribution_settlement.confirmed_at = _utc(2027, 1, 5)

    member = _member(db, "h2b3-loan")
    _loan, installment = _installment(db, member, amount="130.00", interest="20.00", penalty="10.00")
    loan_payment = _payment(db, suffix="h2b3-loan", amount="130.00", reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id))
    loan_settlement = _settle(db, loan_payment, when=_utc(2027, 1, 6))

    agreement_member, _agreement_row, agreement_installments = _agreement(db, principal="100.00", status="OPEN")
    agreement_payment_row = agreement_payment(db, agreement_member, agreement_installments[0], amount="30.00", suffix="h2b3-agreement")
    agreement_payment_row.confirmed_at = _utc(2027, 1, 7)
    agreement_settlement = agreement_settle(db, agreement_payment_row)
    db.commit()

    report = annual_accountability(db, 2027)
    january = report["months"][0]
    assert january["inflows"] == {
        "contributions_paid": "100.00",
        "loan_principal_received": "100.00",
        "interest_received": "20.00",
        "penalties_received": "10.00",
        "agreement_installments_received": "30.00",
    }
    assert set(report["totals"]) == {"contributions_paid", "interest_received", "penalties_received", "expenses", "operating_result"}
    assert report["totals"] == {
        "contributions_paid": "100.00",
        "interest_received": "20.00",
        "penalties_received": "10.00",
        "expenses": "0.00",
        "operating_result": "130.00",
    }
    assert Decimal(report["totals"]["contributions_paid"]) == _sum_inflow(report, "contributions_paid")
    assert Decimal(report["totals"]["interest_received"]) == _sum_inflow(report, "interest_received")
    assert Decimal(report["totals"]["penalties_received"]) == _sum_inflow(report, "penalties_received")
    assert Decimal(report["totals"]["expenses"]) == _sum_monthly(report, "expenses")
    assert Decimal(report["totals"]["operating_result"]) == _sum_monthly(report, "operating_result")
    assert loan_settlement.principal_applied == Decimal("100.00")
    assert agreement_settlement.amount_applied == Decimal("30.00")
    assert "loan_principal_received" not in report["totals"]
    assert "agreement_installments_received" not in report["totals"]


def test_annual_same_year_reversal_preserves_original_month_and_nets_operating_components():
    db = _db()
    admin, _contribution, contribution_payment, contribution_settlement = _setup(db, suffix="h2b3-same-year")
    contribution_settlement.confirmed_at = _utc(2027, 1, 10)
    member = _member(db, "h2b3-same-year-loan")
    _loan, installment = _installment(db, member, amount="130.00", interest="20.00", penalty="10.00")
    loan_payment = _payment(db, suffix="h2b3-same-year-loan", amount="130.00", reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id))
    _settle(db, loan_payment, when=_utc(2027, 1, 11))
    db.commit()

    reverse_payment(db, payment_id=contribution_payment.id, admin_id=admin.id, reason="Estorno anual", now=_utc(2027, 3, 15))
    reverse_payment(db, payment_id=loan_payment.id, admin_id=admin.id, reason="Estorno anual", now=_utc(2027, 3, 16))
    db.commit()

    report = annual_accountability(db, 2027)
    assert report["months"][0]["inflows"]["contributions_paid"] == "100.00"
    assert report["months"][0]["inflows"]["interest_received"] == "20.00"
    assert report["months"][0]["inflows"]["penalties_received"] == "10.00"
    assert report["months"][2]["inflows"]["contributions_paid"] == "-100.00"
    assert report["months"][2]["inflows"]["interest_received"] == "-20.00"
    assert report["months"][2]["inflows"]["penalties_received"] == "-10.00"
    assert report["totals"] == {
        "contributions_paid": "0.00",
        "interest_received": "0.00",
        "penalties_received": "0.00",
        "expenses": "0.00",
        "operating_result": "0.00",
    }
    assert report["months"][0]["inflows"]["loan_principal_received"] == "100.00"
    assert report["months"][2]["inflows"]["loan_principal_received"] == "-100.00"


def test_annual_cross_year_reversal_keeps_december_original_and_january_compensation():
    db = _db()
    admin, _contribution, payment, settlement = _setup(db, suffix="h2b3-cross-year")
    settlement.confirmed_at = _utc(2026, 12, 31, 23)
    db.commit()
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno virada", now=_utc(2027, 1, 1, 0))
    db.commit()

    annual_2026 = annual_accountability(db, 2026)
    annual_2027 = annual_accountability(db, 2027)
    assert annual_2026["months"][11]["inflows"]["contributions_paid"] == "100.00"
    assert annual_2026["totals"]["contributions_paid"] == "100.00"
    assert annual_2027["months"][0]["inflows"]["contributions_paid"] == "-100.00"
    assert annual_2027["totals"]["contributions_paid"] == "-100.00"
    assert reversal.reversal_competence == date(2027, 1, 1)


def test_annual_invalid_and_generic_reversals_do_not_reduce_payment_totals():
    db = _db()
    admin, _contribution, payment, settlement = _setup(db, suffix="h2b3-invalid")
    settlement.confirmed_at = _utc(2027, 5, 10)
    db.commit()
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno inválido", now=_utc(2027, 5, 11))
    db.commit()
    db.execute(text("UPDATE payment_reversals SET receipt_hash = 'invalid' WHERE id = :id"), {"id": reversal.id})
    db.commit()
    invalid_report = annual_accountability(db, 2027)
    assert invalid_report["totals"]["contributions_paid"] == "100.00"

    db2 = _db()
    _admin, _contribution, payment2, settlement2 = _setup(db2, suffix="h2b3-generic")
    original = db2.query(LedgerEntry).filter_by(reference_type="CONTRIBUTION_PAYMENT", reference_id=str(payment2.id)).one()
    reverse_entry(db2, original, "Ajuste genérico anual")
    db2.commit()
    generic_report = annual_accountability(db2, settlement2.confirmed_at.year)
    assert generic_report["totals"]["contributions_paid"] == "100.00"


def test_annual_accountability_is_read_only_and_keeps_closed_closing_immutable():
    db = _db()
    _admin, _contribution, payment, settlement = _setup(db, suffix="h2b3-readonly")
    closing = MonthlyClosing(competence=settlement.confirmed_at.date().replace(day=1), status="CLOSED", total_contributions=Decimal("7.00"))
    db.add(closing)
    db.commit()
    before = {
        "payment": (payment.status, payment.amount, payment.reference_id),
        "settlement": (settlement.receipt_snapshot_json, settlement.receipt_hash, settlement.amount_applied),
        "ledger": [(row.id, row.amount, row.direction, row.entry_hash) for row in db.query(LedgerEntry).all()],
        "mfe": [(row.id, row.amount, row.direction, row.entry_type) for row in db.query(MemberFinancialEntry).all()],
        "closing": (closing.status, closing.total_contributions),
    }
    annual_accountability(db, settlement.confirmed_at.year)
    assert (payment.status, payment.amount, payment.reference_id) == before["payment"]
    assert (settlement.receipt_snapshot_json, settlement.receipt_hash, settlement.amount_applied) == before["settlement"]
    assert [(row.id, row.amount, row.direction, row.entry_hash) for row in db.query(LedgerEntry).all()] == before["ledger"]
    assert [(row.id, row.amount, row.direction, row.entry_type) for row in db.query(MemberFinancialEntry).all()] == before["mfe"]
    assert (closing.status, closing.total_contributions) == before["closing"]
