from datetime import date, datetime, timezone
from decimal import Decimal

from test_payment_settlement_v103 import _db, _installment, _member, _payment, _settle
from app.models import LedgerEntry
from app.services.reconciliation_v040 import build_advanced_reconciliation


def test_interest_received_counts_only_loan_interest_credit_in_competence():
    db = _db()

    member = _member(db, "interest-source")
    _loan, installment = _installment(db, member, amount="120.00", interest="20.00", penalty="0.00")
    payment = _payment(
        db,
        suffix="interest-source",
        amount="120.00",
        reference_type="LOAN_INSTALLMENT",
        reference_id=str(installment.id),
    )

    def add(amount, reference_type, created_at, direction="CREDIT"):
        db.add(
            LedgerEntry(
                account="CAIXINHA",
                direction=direction,
                amount=Decimal(amount),
                reference_type=reference_type,
                reference_id=f"{reference_type}-{amount}",
                created_at=created_at,
            )
        )

    inside = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    outside = datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc)

    _settle(db, payment, when=inside)

    add("777.00", "LOAN_INTEREST_PAYMENT", inside)
    add("10.00", "LOAN_PENALTY_PAYMENT", inside)
    add("50.00", "LOAN_INSTALLMENT_PAYMENT", inside)
    add("30.00", "AGREEMENT_INSTALLMENT_PAYMENT", inside)
    add("99.00", "LOAN_INTEREST_PAYMENT", outside)

    db.commit()

    snapshot = build_advanced_reconciliation(db, date(2026, 9, 1))["snapshot"]

    assert snapshot["interest_received"] == "20.00"

    db.close()
