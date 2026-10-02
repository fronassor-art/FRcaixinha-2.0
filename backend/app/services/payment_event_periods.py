"""Central temporal classification for payment settlement and reversal events.

Legacy event periods remain timestamp-based in UTC. Temporal event periods are
DATE-based only after their F2-E2 receipt has been verified. This module does
not write financial state.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Literal

from sqlalchemy.orm import Session

from app.models import Payment, PaymentReversal, PaymentSettlement
from app.services.payment_reversal_evidence import validate_reversal_effect
from app.services.temporal_event_receipts import verify_settlement_v6


SETTLEMENT_TEMPORAL_VERSION = "v6"
REVERSAL_TEMPORAL_VERSION = "v2"

LEGACY_SETTLEMENT_VERSIONS = {
    "CONTRIBUTION": frozenset({"v1"}),
    "LOAN_INSTALLMENT": frozenset({"v1", "v2", "v3", "v4"}),
    "AGREEMENT_INSTALLMENT": frozenset({"v1", "v5"}),
}
LEGACY_REVERSAL_VERSIONS = frozenset({"v1"})


class PaymentEventEvidenceError(ValueError):
    """An event is temporally unknown or its temporal evidence is invalid."""


class UnsupportedPaymentEventVersion(PaymentEventEvidenceError):
    """A receipt version cannot be interpreted under the current contract."""


@dataclass(frozen=True)
class PaymentEventPeriodIdentity:
    """Verified classification plus both event instant and optional civil date.

    ``financial_date`` is deliberately ``None`` for legacy events. The
    ``timestamp_utc`` remains available for as-of/cutoff readers even when a
    temporal event is selected by its authenticated civil date.
    """

    event_kind: Literal["SETTLEMENT", "REVERSAL"]
    semantics: Literal["LEGACY", "TEMPORAL"]
    timestamp_utc: datetime
    financial_date: date | None
    payment_id: int
    settlement_id: int
    reversal_id: int | None
    obligation_type: str
    evidence_status: Literal["LEGACY_COMPATIBILITY", "LEGACY_VERIFIED", "TEMPORAL_VERIFIED"]
    evidence_detail: str | None = None


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def classify_settlement_event(
    db: Session, payment: Payment, settlement: PaymentSettlement
) -> PaymentEventPeriodIdentity | None:
    """Classify a settlement; temporal v6 is returned only after verification.

    Legacy receipts retain their historical acceptance contract. The caller
    remains responsible for the existing payment/obligation consistency
    checks; this function does not claim that legacy receipts were verified
    with the newer F2-E2 evidence contract.
    """
    if settlement.payment_id != payment.id:
        raise PaymentEventEvidenceError("settlement does not reference its Payment")
    timestamp = _utc(settlement.confirmed_at)
    version = settlement.receipt_version

    if version == SETTLEMENT_TEMPORAL_VERSION:
        if settlement.financial_date is None:
            raise PaymentEventEvidenceError("settlement v6 has no financial_date")
        valid, detail = verify_settlement_v6(db, payment, settlement)
        if not valid:
            raise PaymentEventEvidenceError(f"settlement v6 evidence invalid: {detail}")
        if timestamp is None:
            raise PaymentEventEvidenceError("settlement v6 has no confirmed_at")
        return PaymentEventPeriodIdentity(
            event_kind="SETTLEMENT", semantics="TEMPORAL", timestamp_utc=timestamp,
            financial_date=settlement.financial_date, payment_id=payment.id,
            settlement_id=settlement.id, reversal_id=None,
            obligation_type=settlement.obligation_type,
            evidence_status="TEMPORAL_VERIFIED",
        )

    allowed = LEGACY_SETTLEMENT_VERSIONS.get(settlement.obligation_type, frozenset())
    if version not in allowed:
        raise UnsupportedPaymentEventVersion(
            f"unknown or unsupported settlement receipt version: {version!r}"
        )
    if settlement.financial_date is not None:
        raise PaymentEventEvidenceError("legacy settlement unexpectedly has financial_date")
    if timestamp is None:
        return None
    return PaymentEventPeriodIdentity(
        event_kind="SETTLEMENT", semantics="LEGACY", timestamp_utc=timestamp,
        financial_date=None, payment_id=payment.id, settlement_id=settlement.id,
        reversal_id=None, obligation_type=settlement.obligation_type,
        evidence_status="LEGACY_COMPATIBILITY",
    )


def classify_reversal_event(
    db: Session, reversal: PaymentReversal
) -> PaymentEventPeriodIdentity | None:
    """Classify a reversal independently from its settlement's period mode.

    Invalid legacy reversals preserve prior behavior: they have no negative
    event, while the original remains visible. Temporal v2 cannot be silently
    omitted because its date is used to select an economic period.
    """
    version = reversal.receipt_version
    if version not in LEGACY_REVERSAL_VERSIONS | {REVERSAL_TEMPORAL_VERSION}:
        raise UnsupportedPaymentEventVersion(
            f"unknown reversal receipt version: {version!r}"
        )
    temporal = version == REVERSAL_TEMPORAL_VERSION
    if temporal and reversal.financial_date is None:
        raise PaymentEventEvidenceError("reversal v2 has no financial_date")
    if not temporal and reversal.financial_date is not None:
        raise PaymentEventEvidenceError("legacy reversal unexpectedly has financial_date")

    timestamp = _utc(reversal.reversed_at)
    if timestamp is None:
        if temporal:
            raise PaymentEventEvidenceError("reversal v2 has no reversed_at")
        return None

    valid, detail = validate_reversal_effect(db, reversal)
    if temporal and not valid:
        raise PaymentEventEvidenceError(f"reversal v2 evidence invalid: {detail}")

    settlement = db.get(PaymentSettlement, reversal.settlement_id)
    if settlement is None:
        if temporal:
            raise PaymentEventEvidenceError("reversal v2 settlement is missing")
        return None

    if temporal:
        return PaymentEventPeriodIdentity(
            event_kind="REVERSAL", semantics="TEMPORAL", timestamp_utc=timestamp,
            financial_date=reversal.financial_date, payment_id=reversal.payment_id,
            settlement_id=reversal.settlement_id, reversal_id=reversal.id,
            obligation_type=settlement.obligation_type,
            evidence_status="TEMPORAL_VERIFIED",
        )

    return PaymentEventPeriodIdentity(
        event_kind="REVERSAL", semantics="LEGACY", timestamp_utc=timestamp,
        financial_date=None, payment_id=reversal.payment_id,
        settlement_id=reversal.settlement_id, reversal_id=reversal.id,
        obligation_type=settlement.obligation_type,
        evidence_status="LEGACY_VERIFIED" if valid else "LEGACY_COMPATIBILITY",
        evidence_detail=None if valid else detail,
    )


def civil_date_window(
    start: datetime | None,
    end: datetime | None,
    *,
    financial_start: date | None = None,
    financial_end_exclusive: date | None = None,
) -> tuple[date | None, date | None]:
    """Resolve explicit civil DATE bounds or date-labelled UTC-midnight bounds.

    Existing monthly callers pass UTC midnight boundaries; their date labels
    are the requested competence dates. Sub-day timestamp windows cannot be
    represented faithfully by DATE and therefore require explicit civil
    bounds when temporal events are read.
    """
    if financial_start is not None and isinstance(financial_start, datetime):
        raise TypeError("financial_start must be a civil date, not datetime")
    if financial_end_exclusive is not None and isinstance(financial_end_exclusive, datetime):
        raise TypeError("financial_end_exclusive must be a civil date, not datetime")

    def from_boundary(value: datetime | None) -> date | None:
        normalized = _utc(value)
        if normalized is None:
            return None
        if normalized.timetz().replace(tzinfo=None) != time.min:
            raise ValueError(
                "temporal DATE events require civil date bounds for sub-day windows"
            )
        return normalized.date()

    lower = financial_start if financial_start is not None else from_boundary(start)
    upper = (
        financial_end_exclusive
        if financial_end_exclusive is not None
        else from_boundary(end)
    )
    if lower is not None and upper is not None and upper < lower:
        raise ValueError("financial date window end precedes start")
    return lower, upper


def event_is_in_period(
    identity: PaymentEventPeriodIdentity,
    *,
    start: datetime | None,
    end: datetime | None,
    financial_start: date | None = None,
    financial_end_exclusive: date | None = None,
) -> bool:
    """Apply the distinct legacy UTC or temporal civil DATE period contract."""
    if identity.semantics == "LEGACY":
        timestamp = identity.timestamp_utc
        if start is not None and timestamp < _utc(start):
            return False
        if end is not None and timestamp >= _utc(end):
            return False
        return True

    if identity.financial_date is None:
        raise PaymentEventEvidenceError("verified temporal event has no financial_date")
    lower, upper = civil_date_window(
        start, end,
        financial_start=financial_start,
        financial_end_exclusive=financial_end_exclusive,
    )
    if lower is not None and identity.financial_date < lower:
        return False
    if upper is not None and identity.financial_date >= upper:
        return False
    return True
