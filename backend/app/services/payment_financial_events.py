"""Read-only financial events derived from immutable payment evidence.

This module deliberately does not decide whether a reversal is valid.  That
decision belongs to :func:`validate_reversal_effect`; this layer only turns
settlements and validated reversals into analytical events.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import (
    AgreementInstallment,
    CollectionAgreement,
    Contribution,
    Loan,
    LoanInstallment,
    Payment,
    PaymentReversal,
    PaymentSettlement,
)
from app.services.payment_reversal_evidence import validate_reversal_effect


ZERO = Decimal("0.00")

# Historical settlement versions whose persisted allocation fields are enough
# to describe an original payment event.  Reversal eligibility is narrower and
# remains enforced by the H1 validator.
SUPPORTED_SETTLEMENT_VERSIONS = {
    "CONTRIBUTION": frozenset({"v1"}),
    "LOAN_INSTALLMENT": frozenset({"v1", "v2", "v3", "v4"}),
    "AGREEMENT_INSTALLMENT": frozenset({"v1", "v5"}),
}


@dataclass(frozen=True)
class PaymentFinancialEvent:
    """An immutable, signed analytical event; no ORM object is exposed."""

    event_id: str
    payment_id: int
    settlement_id: int
    payment_reversal_id: int | None
    component: str
    amount: Decimal
    occurred_at: datetime
    source: str


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _in_window(value: datetime, start: datetime | None, end: datetime | None) -> bool:
    value = _utc(value)
    if start is not None and value < _utc(start):
        return False
    if end is not None and value >= _utc(end):
        return False
    return True


def _components(settlement: PaymentSettlement) -> tuple[tuple[str, Decimal], ...]:
    if settlement.obligation_type == "CONTRIBUTION":
        return (("CONTRIBUTION", Decimal(settlement.amount_applied or 0)),)
    if settlement.obligation_type == "LOAN_INSTALLMENT":
        return (
            ("LOAN_PRINCIPAL", Decimal(settlement.principal_applied or 0)),
            ("LOAN_INTEREST", Decimal(settlement.interest_applied or 0)),
            ("LOAN_PENALTY", Decimal(settlement.penalty_applied or 0)),
        )
    if settlement.obligation_type == "AGREEMENT_INSTALLMENT":
        return (("AGREEMENT", Decimal(settlement.amount_applied or 0)),)
    return ()


def _supported(settlement: PaymentSettlement) -> bool:
    return settlement.receipt_version in SUPPORTED_SETTLEMENT_VERSIONS.get(
        settlement.obligation_type, frozenset()
    )


def _original_is_consistent(db: Session, settlement: PaymentSettlement) -> bool:
    """Check the immutable settlement's Payment and obligation references."""
    payment = db.get(Payment, settlement.payment_id)
    if payment is None or payment.status != "approved":
        return False

    expected = {
        "CONTRIBUTION": (payment.reference_type, payment.reference_id, settlement.contribution_id),
        "LOAN_INSTALLMENT": (payment.reference_type, payment.reference_id, settlement.loan_installment_id),
        "AGREEMENT_INSTALLMENT": (payment.reference_type, payment.reference_id, settlement.agreement_installment_id),
    }.get(settlement.obligation_type)
    if expected is None:
        return False
    reference_type, reference_id, target_id = expected
    if reference_type != settlement.obligation_type or reference_id != str(target_id) or target_id is None:
        return False

    if settlement.obligation_type == "CONTRIBUTION":
        target = db.get(Contribution, target_id)
        return target is not None and target.member_id == settlement.member_id
    if settlement.obligation_type == "LOAN_INSTALLMENT":
        target = db.get(LoanInstallment, target_id)
        loan = db.get(Loan, target.loan_id) if target is not None else None
        return loan is not None and loan.member_id == settlement.member_id
    target = db.get(AgreementInstallment, target_id)
    agreement = db.get(CollectionAgreement, target.agreement_id) if target is not None else None
    return agreement is not None and agreement.member_id == settlement.member_id


def _event(
    settlement: PaymentSettlement,
    component: str,
    amount: Decimal,
    occurred_at: datetime,
    source: str,
    reversal_id: int | None = None,
) -> PaymentFinancialEvent | None:
    if amount == ZERO:
        return None
    suffix = f":reversal:{reversal_id}" if reversal_id is not None else ":original"
    return PaymentFinancialEvent(
        event_id=f"settlement:{settlement.id}:{component}{suffix}",
        payment_id=settlement.payment_id,
        settlement_id=settlement.id,
        payment_reversal_id=reversal_id,
        component=component,
        amount=amount,
        occurred_at=_utc(occurred_at),
        source=source,
    )


def payment_financial_events(
    db: Session,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    member_id: int | None = None,
) -> tuple[PaymentFinancialEvent, ...]:
    """Return original and valid-reversal events in a UTC half-open window.

    Originals and reversals are filtered independently, so a reversal in a
    later month is still found even when its original settlement is older.
    All database reads are protected from autoflush and this function never
    mutates, flushes, locks, or commits.
    """
    with db.no_autoflush:
        settlement_query = db.query(PaymentSettlement).order_by(PaymentSettlement.id)
        if member_id is not None:
            settlement_query = settlement_query.filter(PaymentSettlement.member_id == member_id)
        settlements = settlement_query.all()

        reversal_query = db.query(PaymentReversal).order_by(PaymentReversal.id)
        reversals = reversal_query.all()

    with db.no_autoflush:
        by_settlement = {settlement.id: settlement for settlement in settlements}
        candidates: list[tuple[PaymentReversal, PaymentSettlement]] = []
        for reversal in reversals:
            settlement = by_settlement.get(reversal.settlement_id)
            if settlement is None:
                settlement = db.get(PaymentSettlement, reversal.settlement_id)
                if settlement is None or (member_id is not None and settlement.member_id != member_id):
                    continue
                by_settlement[settlement.id] = settlement
            if _in_window(reversal.reversed_at, start, end):
                candidates.append((reversal, settlement))

        events: list[PaymentFinancialEvent] = []
        candidate_settlement_ids = {reversal.settlement_id for reversal, _ in candidates}
        for settlement in by_settlement.values():
            if not _supported(settlement) or not _original_is_consistent(db, settlement):
                continue
            if _in_window(settlement.confirmed_at, start, end) or settlement.id in candidate_settlement_ids:
                for component, amount in _components(settlement):
                    item = _event(settlement, component, amount, settlement.confirmed_at, "ORIGINAL")
                    if item is not None and _in_window(item.occurred_at, start, end):
                        events.append(item)

        for reversal, settlement in candidates:
            if not _supported(settlement):
                continue
            valid, _detail = validate_reversal_effect(db, reversal)
            if not valid:
                continue
            for component, amount in _components(settlement):
                item = _event(
                    settlement,
                    component,
                    -amount,
                    reversal.reversed_at,
                    "REVERSAL",
                    reversal.id,
                )
                if item is not None:
                    events.append(item)

        return tuple(sorted(events, key=lambda item: (item.occurred_at, item.event_id)))
