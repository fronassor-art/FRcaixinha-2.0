"""Read-only financial events derived from immutable payment evidence.

This module deliberately does not decide whether a reversal is valid.  That
decision belongs to :func:`validate_reversal_effect`; this layer only turns
settlements and validated reversals into analytical events.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import and_, or_
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
from app.services.payment_event_periods import (
    LEGACY_SETTLEMENT_VERSIONS,
    PaymentEventPeriodIdentity,
    classify_reversal_event,
    classify_settlement_event,
    event_is_in_period,
)


ZERO = Decimal("0.00")

# Historical settlement versions whose persisted allocation fields are enough
# to describe an original payment event.  Reversal eligibility is narrower and
# remains enforced by the H1 validator.
SUPPORTED_SETTLEMENT_VERSIONS = LEGACY_SETTLEMENT_VERSIONS


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
    semantics: str = "LEGACY"
    financial_date: date | None = None
    event_kind: str = "SETTLEMENT"
    evidence_status: str = "LEGACY_COMPATIBILITY"
    obligation_type: str | None = None


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
    identity: PaymentEventPeriodIdentity | None = None,
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
        semantics=identity.semantics if identity is not None else "LEGACY",
        financial_date=identity.financial_date if identity is not None else None,
        event_kind=identity.event_kind if identity is not None else source,
        evidence_status=identity.evidence_status if identity is not None else "LEGACY_COMPATIBILITY",
        obligation_type=identity.obligation_type if identity is not None else settlement.obligation_type,
    )


def _candidate_query(query, timestamp_column, financial_date_column, version_column, *,
                     start, end, financial_start, financial_end_exclusive,
                     temporal_version, partial_window):
    """Bound candidates on either the legacy timestamp or temporal DATE axis."""
    clauses = []
    timestamp_filters = []
    if start is not None:
        timestamp_filters.append(timestamp_column >= _utc(start))
    if end is not None:
        timestamp_filters.append(timestamp_column < _utc(end))
    if timestamp_filters:
        clauses.append(and_(*timestamp_filters))

    date_filters = []
    if financial_start is not None:
        date_filters.append(financial_date_column >= financial_start)
    if financial_end_exclusive is not None:
        date_filters.append(financial_date_column < financial_end_exclusive)
    if date_filters:
        clauses.append(and_(*date_filters))

    # With sub-day legacy bounds there is no exact DATE equivalent. Fetch only
    # temporal-version candidates whose authenticated timestamp could map to
    # a date near the requested window, then fail closed when classification
    # reaches one. A one-day envelope safely contains the Belem UTC offset.
    if partial_window:
        envelope = []
        if start is not None:
            envelope.append(timestamp_column >= _utc(start) - timedelta(days=1))
        if end is not None:
            envelope.append(timestamp_column < _utc(end) + timedelta(days=1))
        clauses.append(and_(version_column == temporal_version, *envelope))

    if not clauses:
        return query
    return query.filter(or_(*clauses))


def payment_financial_events(
    db: Session,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    member_id: int | None = None,
    financial_start: date | None = None,
    financial_end_exclusive: date | None = None,
) -> tuple[PaymentFinancialEvent, ...]:
    """Return original and validated reversal events in mixed period semantics.

    Legacy events retain the UTC half-open timestamp window. Temporal v6/v2
    events use authenticated civil DATE bounds. Existing monthly callers pass
    UTC-midnight boundaries whose ISO date labels identify the requested
    competence; sub-day requests that encounter temporal rows must provide
    explicit DATE bounds. Originals and reversals are filtered independently.
    All database reads are protected from autoflush and this function never
    mutates, flushes, locks, or commits.
    """
    from app.services.payment_event_periods import civil_date_window

    utc_start = _utc(start) if start is not None else None
    utc_end = _utc(end) if end is not None else None
    if utc_start is not None and utc_end is not None and utc_end < utc_start:
        raise ValueError("UTC event window end precedes start")
    if (financial_start is not None or financial_end_exclusive is not None) and (
        utc_start is None or utc_end is None
    ):
        raise ValueError("explicit financial DATE bounds require matching UTC bounds for legacy events")

    partial_window = any(
        value is not None and value.timetz().replace(tzinfo=None) != time.min
        for value in (utc_start, utc_end)
    )
    if partial_window and (financial_start is not None or financial_end_exclusive is not None):
        # Explicit civil bounds remove the ambiguity for the temporal branch.
        partial_window = False
    if partial_window:
        # Keep legacy-only sub-day API calls compatible. Temporal rows are
        # separately selected via a bounded timestamp envelope and rejected
        # below unless callers supply explicit financial DATE bounds.
        resolved_start = resolved_end = None
    else:
        resolved_start, resolved_end = civil_date_window(
            utc_start, utc_end,
            financial_start=financial_start,
            financial_end_exclusive=financial_end_exclusive,
        )

    with db.no_autoflush:
        settlement_query = db.query(PaymentSettlement).order_by(PaymentSettlement.id)
        if member_id is not None:
            settlement_query = settlement_query.filter(PaymentSettlement.member_id == member_id)
        settlement_query = _candidate_query(
            settlement_query, PaymentSettlement.confirmed_at,
            PaymentSettlement.financial_date, PaymentSettlement.receipt_version,
            start=utc_start, end=utc_end,
            financial_start=resolved_start, financial_end_exclusive=resolved_end,
            temporal_version="v6", partial_window=partial_window,
        )
        settlements = settlement_query.all()

        reversal_query = db.query(PaymentReversal).order_by(PaymentReversal.id)
        reversal_query = _candidate_query(
            reversal_query, PaymentReversal.reversed_at,
            PaymentReversal.financial_date, PaymentReversal.receipt_version,
            start=utc_start, end=utc_end,
            financial_start=resolved_start, financial_end_exclusive=resolved_end,
            temporal_version="v2", partial_window=partial_window,
        )
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
            identity = classify_reversal_event(db, reversal)
            if identity is None:
                continue
            if event_is_in_period(
                identity, start=utc_start, end=utc_end,
                financial_start=resolved_start, financial_end_exclusive=resolved_end,
            ):
                candidates.append((reversal, settlement, identity))

        events: list[PaymentFinancialEvent] = []
        for settlement in by_settlement.values():
            payment = db.get(Payment, settlement.payment_id)
            if payment is None:
                continue
            identity = classify_settlement_event(db, payment, settlement)
            if identity is None or not _original_is_consistent(db, settlement):
                continue
            if event_is_in_period(
                identity, start=utc_start, end=utc_end,
                financial_start=resolved_start, financial_end_exclusive=resolved_end,
            ):
                for component, amount in _components(settlement):
                    item = _event(
                        settlement, component, amount, identity.timestamp_utc,
                        "ORIGINAL", identity=identity,
                    )
                    if item is not None:
                        events.append(item)

        for reversal, settlement, identity in candidates:
            if identity.evidence_status in {"LEGACY_COMPATIBILITY"}:
                continue
            for component, amount in _components(settlement):
                item = _event(
                    settlement,
                    component,
                    -amount,
                    identity.timestamp_utc,
                    "REVERSAL",
                    reversal.id,
                    identity,
                )
                if item is not None:
                    events.append(item)

        return tuple(sorted(events, key=lambda item: (item.occurred_at, item.event_id)))
