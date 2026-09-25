"""Immutable, read-only annual closing statements from the official snapshot."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CycleAnnualClosing,
    CycleAnnualClosingReview,
    CycleAnnualClosingSnapshot,
)
from app.services.cycle_closing_payout_obligations import (
    PayoutObligationConflict,
    verify_cycle_annual_closing_payout_obligations_read_only,
)
from app.services.cycle_closing_persistence import verify_cycle_annual_closing_snapshot
from app.services.cycle_closing_workflow import verify_cycle_annual_closing_review_read_only


CENT = Decimal("0.01")
ZERO = Decimal("0.00")
CLOSED_FORWARD_STATES = frozenset({"CLOSED", "PAYING", "LIQUIDATED"})
OBLIGATION_KINDS = frozenset({
    "CONTRIBUTION_PRINCIPAL", "CONTRIBUTION_CHARGE",
    "LOAN_INSTALLMENT", "AGREEMENT_INSTALLMENT",
})


class ClosingStatementError(ValueError):
    """Stored closing evidence cannot safely produce an official statement."""

    def __init__(self, message: str, *, reason_code: str):
        self.reason_code = reason_code
        super().__init__(message)


@dataclass(frozen=True)
class CompensableObligation:
    source_id: str
    kind: str
    amount: Decimal
    due_date: date
    loan_id: int | None
    agreement_id: int | None


@dataclass(frozen=True)
class ParticipantStatement:
    member_id: int
    cycle_participation_id: int
    participation_status: str
    eligible_contributions: Decimal
    rateio_eligible_contribution_principal: Decimal
    refundable_contribution_principal: Decimal
    weight: Decimal
    gross_share: Decimal
    gross_entitlement: Decimal
    compensable_obligations: tuple[CompensableObligation, ...]
    compensable_obligations_total: Decimal
    projected_compensation: Decimal
    projected_net: Decimal
    projected_residual_debt: Decimal


@dataclass(frozen=True)
class ContributionTrace:
    contribution_id: int
    member_id: int
    amount: Decimal
    confirmed_net_at_cutoff: Decimal
    eligible: bool
    refundable_principal: Decimal
    event_ids: tuple[str, ...]


@dataclass(frozen=True)
class GainTrace:
    source_id: str
    source_type: str
    kind: str
    amount: Decimal
    occurred_at: str
    reason: str | None = None


@dataclass(frozen=True)
class SourceTrace:
    contributions: tuple[ContributionTrace, ...]
    included_gains: tuple[GainTrace, ...]
    excluded_gains: tuple[GainTrace, ...]
    own_balance_settled_loan_ids: tuple[int, ...]


@dataclass(frozen=True)
class SourceGap:
    code: str
    source: str


@dataclass(frozen=True)
class ClosingStatement:
    closing_id: int
    cycle_id: int
    snapshot_id: int
    snapshot_version: str
    payload_hash: str
    calculation_version: str
    calculation_hash: str
    cycle_start_date: date
    closing_cutoff_at: datetime
    financial_timezone: str
    gross_realized_result: Decimal
    administration_fee_rate: Decimal
    administration_fee: Decimal
    distributable_result: Decimal
    total_eligible_contributions: Decimal
    participant_count: int
    total_gross_share: Decimal
    total_gross_entitlement: Decimal
    total_compensable_obligations: Decimal
    total_compensation: Decimal
    total_projected_net: Decimal
    total_projected_residual_debt: Decimal
    approved_review_id: int
    review_version: int
    review_hash: str
    reconciliation_hash: str
    participant_payout_liability: Decimal
    ledger_cash_balance: Decimal
    actual_cash_balance: Decimal
    reconciliation_difference: Decimal
    required_liquidity: Decimal
    liquidity_surplus: Decimal
    source_gaps: tuple[SourceGap, ...]
    unavailable_result_sources: tuple[str, ...]
    participants: tuple[ParticipantStatement, ...]
    payout_obligations_verified: bool | None


@dataclass(frozen=True)
class IndividualClosingStatement:
    closing_id: int
    cycle_id: int
    snapshot_id: int
    snapshot_version: str
    payload_hash: str
    calculation_version: str
    calculation_hash: str
    cycle_start_date: date
    closing_cutoff_at: datetime
    financial_timezone: str
    approved_review_id: int
    review_version: int
    review_hash: str
    reconciliation_hash: str
    participant: ParticipantStatement
    payout_obligations_verified: bool | None


@dataclass(frozen=True)
class CalculationMemory:
    closing_id: int
    cycle_id: int
    snapshot_id: int
    snapshot_version: str
    payload_hash: str
    calculation_version: str
    calculation_hash: str
    cycle_start_date: date
    closing_cutoff_at: datetime
    financial_timezone: str
    gross_realized_result: Decimal
    administration_fee_rate: Decimal
    administration_fee: Decimal
    distributable_result: Decimal
    total_eligible_contributions: Decimal
    participants: tuple[ParticipantStatement, ...]
    source_trace: SourceTrace
    source_gaps: tuple[SourceGap, ...]
    unavailable_result_sources: tuple[str, ...]


@dataclass(frozen=True)
class _VerifiedSource:
    closing: CycleAnnualClosing
    snapshot: CycleAnnualClosingSnapshot
    review: CycleAnnualClosingReview
    payload: dict
    participants: tuple[ParticipantStatement, ...]
    source_trace: SourceTrace
    source_gaps: tuple[SourceGap, ...]
    unavailable_result_sources: tuple[str, ...]
    cycle_start_date: date
    closing_cutoff_at: datetime


def _fail(reason_code: str, message: str) -> ClosingStatementError:
    return ClosingStatementError(message, reason_code=reason_code)


def _decimal(value: object, field: str, *, cents: bool = True, nonnegative: bool = True) -> Decimal:
    if not isinstance(value, str):
        raise _fail("SNAPSHOT_INVALID", f"{field} must be stored as a Decimal string")
    try:
        result = Decimal(value)
        if not result.is_finite() or (cents and result.quantize(CENT) != result):
            raise ValueError
        if nonnegative and result < ZERO:
            raise ValueError
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise _fail("SNAPSHOT_INVALID", f"{field} is not a valid exact amount") from exc
    return result


def _stored_decimal(value: object, field: str, *, nonnegative: bool = True) -> Decimal:
    try:
        valid = isinstance(value, Decimal) and value.is_finite() and value.quantize(CENT) == value
    except InvalidOperation:
        valid = False
    if not valid:
        raise _fail("REVIEW_INVALID", f"{field} must be finite Decimal cents")
    if nonnegative and value < ZERO:
        raise _fail("REVIEW_INVALID", f"{field} must be non-negative")
    return value


def _positive_id(value: object, field: str, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _fail("SNAPSHOT_INVALID", f"{field} is not a positive integer")
    return value


def _json_without_float(raw: str, reason: str) -> dict:
    def reject_float(_value: str):
        raise _fail(reason, "floating point values are forbidden in financial JSON")

    try:
        value = json.loads(raw, parse_float=reject_float)
    except ClosingStatementError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _fail(reason, "financial JSON is invalid") from exc
    if not isinstance(value, dict):
        raise _fail(reason, "financial JSON must be an object")
    return value


def _date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise _fail("SNAPSHOT_INVALID", f"{field} is invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise _fail("SNAPSHOT_INVALID", f"{field} is invalid") from exc
    if parsed.isoformat() != value:
        raise _fail("SNAPSHOT_INVALID", f"{field} is not canonical")
    return parsed


def _datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise _fail("SNAPSHOT_INVALID", f"{field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _fail("SNAPSHOT_INVALID", f"{field} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _fail("SNAPSHOT_INVALID", f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _stored_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise _fail("REVIEW_INVALID", "stored cutoff is invalid")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _participant(row: object) -> ParticipantStatement:
    if not isinstance(row, dict):
        raise _fail("SNAPSHOT_INVALID", "participant entry is invalid")
    member_id = _positive_id(row.get("member_id"), "member_id")
    participation_id = _positive_id(row.get("cycle_participation_id"), "cycle_participation_id")
    status = row.get("participation_status")
    if status not in {"ACTIVE", "BLOCKED_DELINQUENCY", "VOLUNTARILY_EXITED"}:
        raise _fail("SNAPSHOT_INVALID", "participant status is unsupported")

    money_fields = (
        "eligible_contributions", "rateio_eligible_contribution_principal",
        "refundable_contribution_principal", "gross_share", "gross_entitlement",
        "compensable_obligations_total", "projected_compensation", "projected_net",
        "projected_residual_debt",
    )
    values = {field: _decimal(row.get(field), f"participant.{field}") for field in money_fields}
    weight = _decimal(row.get("weight"), "participant.weight", cents=False)
    raw_obligations = row.get("compensable_obligations")
    if not isinstance(raw_obligations, list):
        raise _fail("SNAPSHOT_INVALID", "participant obligations are invalid")
    obligations: list[CompensableObligation] = []
    for item in raw_obligations:
        if not isinstance(item, dict):
            raise _fail("SNAPSHOT_INVALID", "compensable obligation is invalid")
        kind = item.get("kind")
        source_id = item.get("source_id")
        if kind not in OBLIGATION_KINDS or not isinstance(source_id, str) or not source_id:
            raise _fail("SNAPSHOT_INVALID", "compensable obligation identity is invalid")
        due_date = _date(item.get("due_date"), "obligation.due_date")
        loan_id = _positive_id(item.get("loan_id"), "obligation.loan_id", nullable=True)
        agreement_id = _positive_id(item.get("agreement_id"), "obligation.agreement_id", nullable=True)
        obligations.append(CompensableObligation(
            source_id=source_id,
            kind=kind,
            amount=_decimal(item.get("amount"), "obligation.amount"),
            due_date=due_date,
            loan_id=loan_id,
            agreement_id=agreement_id,
        ))
    obligations.sort(key=lambda item: (item.due_date, item.kind, item.source_id))

    owed_total = sum((item.amount for item in obligations), ZERO)
    gross_entitlement = values["refundable_contribution_principal"] + values["gross_share"]
    compensation = min(gross_entitlement, values["compensable_obligations_total"])
    projected_net = gross_entitlement - compensation
    residual_debt = max(ZERO, values["compensable_obligations_total"] - gross_entitlement)
    if (
        owed_total != values["compensable_obligations_total"]
        or values["gross_entitlement"] != gross_entitlement
        or values["projected_compensation"] != compensation
        or values["projected_net"] != projected_net
        or values["projected_residual_debt"] != residual_debt
    ):
        raise _fail("SNAPSHOT_INVALID", "participant calculation memory is inconsistent")

    return ParticipantStatement(
        member_id=member_id,
        cycle_participation_id=participation_id,
        participation_status=status,
        weight=weight,
        compensable_obligations=tuple(obligations),
        **values,
    )


def _trace(payload: dict) -> SourceTrace:
    raw = payload.get("source_trace")
    if not isinstance(raw, dict):
        raise _fail("SNAPSHOT_INVALID", "source trace is invalid")
    contributions = raw.get("contributions")
    included = raw.get("included_gains")
    excluded = raw.get("excluded_gains")
    settled = raw.get("own_balance_settled_loan_ids")
    if not isinstance(contributions, list) or not isinstance(included, list) or not isinstance(excluded, list):
        raise _fail("SNAPSHOT_INVALID", "source trace entries are invalid")
    if not isinstance(settled, list):
        raise _fail("SNAPSHOT_INVALID", "settled loan trace is invalid")

    contribution_rows: list[ContributionTrace] = []
    for row in contributions:
        if not isinstance(row, dict) or not isinstance(row.get("eligible"), bool):
            raise _fail("SNAPSHOT_INVALID", "contribution trace is invalid")
        events = row.get("event_ids")
        if not isinstance(events, list) or any(not isinstance(event, str) for event in events):
            raise _fail("SNAPSHOT_INVALID", "contribution event trace is invalid")
        contribution_rows.append(ContributionTrace(
            contribution_id=_positive_id(row.get("contribution_id"), "contribution_id"),
            member_id=_positive_id(row.get("member_id"), "contribution.member_id"),
            amount=_decimal(row.get("amount"), "contribution.amount"),
            confirmed_net_at_cutoff=_decimal(row.get("confirmed_net_at_cutoff"), "confirmed_net_at_cutoff"),
            eligible=row["eligible"],
            refundable_principal=_decimal(row.get("refundable_principal"), "refundable_principal"),
            event_ids=tuple(events),
        ))
    contribution_rows.sort(key=lambda item: (item.member_id, item.contribution_id))

    def gains(rows: list, *, excluded_gain: bool) -> tuple[GainTrace, ...]:
        result: list[GainTrace] = []
        for row in rows:
            if not isinstance(row, dict):
                raise _fail("SNAPSHOT_INVALID", "gain trace is invalid")
            fields = (row.get("source_id"), row.get("source_type"), row.get("kind"), row.get("occurred_at"))
            if any(not isinstance(value, str) or not value for value in fields):
                raise _fail("SNAPSHOT_INVALID", "gain trace identity is invalid")
            reason = row.get("reason") if excluded_gain else None
            if excluded_gain and (not isinstance(reason, str) or not reason):
                raise _fail("SNAPSHOT_INVALID", "excluded gain reason is invalid")
            result.append(GainTrace(
                source_id=fields[0], source_type=fields[1], kind=fields[2],
                amount=_decimal(row.get("amount"), "gain.amount"),
                occurred_at=fields[3], reason=reason,
            ))
        result.sort(key=lambda item: (item.source_type, item.source_id, item.kind))
        return tuple(result)

    settled_ids = tuple(_positive_id(item, "own_balance_settled_loan_id") for item in settled)
    if len(set(settled_ids)) != len(settled_ids):
        raise _fail("SNAPSHOT_INVALID", "settled loan trace contains duplicates")
    gaps = payload.get("source_gaps")
    if not isinstance(gaps, list):
        raise _fail("SNAPSHOT_INVALID", "source gaps are invalid")
    return SourceTrace(
        contributions=tuple(contribution_rows),
        included_gains=gains(included, excluded_gain=False),
        excluded_gains=gains(excluded, excluded_gain=True),
        own_balance_settled_loan_ids=tuple(sorted(settled_ids)),
    )


def _load_verified_source(db: Session, closing_id: int) -> _VerifiedSource:
    closing = db.get(CycleAnnualClosing, closing_id)
    if closing is None:
        raise _fail("CLOSING_NOT_FOUND", "annual closing was not found")
    if closing.status not in CLOSED_FORWARD_STATES or closing.closed_at is None or closing.closed_by is None:
        raise _fail("CLOSING_NOT_CLOSED", "annual closing has not reached CLOSED")
    if (
        closing.approved_review_id is None or closing.approved_at is None
        or closing.approved_by is None
    ):
        raise _fail("REVIEW_INVALID", "annual closing approval linkage is incomplete")

    snapshots = db.execute(
        select(CycleAnnualClosingSnapshot).where(
            CycleAnnualClosingSnapshot.closing_id == closing.id,
        )
    ).scalars().all()
    if len(snapshots) != 1:
        raise _fail("SNAPSHOT_INVALID", "annual closing must have exactly one snapshot")
    snapshot = snapshots[0]
    try:
        verify_cycle_annual_closing_snapshot(snapshot)
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise _fail("SNAPSHOT_INVALID", "official snapshot integrity verification failed") from exc
    if (
        snapshot.closing_id != closing.id or snapshot.cycle_id != closing.cycle_id
        or snapshot.created_by != closing.closed_by
    ):
        raise _fail("SNAPSHOT_INVALID", "snapshot closing/cycle linkage is inconsistent")
    payload = _json_without_float(snapshot.canonical_payload, "SNAPSHOT_INVALID")

    review = db.get(CycleAnnualClosingReview, closing.approved_review_id)
    if (
        review is None or review.id != closing.approved_review_id
        or review.closing_id != closing.id or review.cycle_id != closing.cycle_id
        or not verify_cycle_annual_closing_review_read_only(review)
    ):
        raise _fail("REVIEW_INVALID", "approved review linkage or integrity is invalid")

    cutoff = _datetime(payload.get("closing_cutoff_at"), "closing_cutoff_at")
    review_cutoff = _stored_utc(review.closing_cutoff_at)
    if (
        payload.get("cycle_id") != closing.cycle_id
        or payload.get("calculation_version") != snapshot.calculation_version
        or snapshot.calculation_version != review.calculation_version
        or payload.get("calculation_hash") != review.calculation_hash
        or cutoff != _stored_utc(snapshot.closing_cutoff_at)
        or cutoff != review_cutoff
    ):
        raise _fail("SNAPSHOT_REVIEW_MISMATCH", "snapshot and approved review do not match")

    raw_participants = payload.get("participants")
    if not isinstance(raw_participants, list):
        raise _fail("SNAPSHOT_INVALID", "snapshot participants are invalid")
    participants = tuple(sorted(
        (_participant(row) for row in raw_participants),
        key=lambda row: (row.cycle_participation_id, row.member_id),
    ))
    if len({row.member_id for row in participants}) != len(participants):
        raise _fail("SNAPSHOT_INVALID", "snapshot contains duplicate Member identities")
    if len({row.cycle_participation_id for row in participants}) != len(participants):
        raise _fail("SNAPSHOT_INVALID", "snapshot contains duplicate participation identities")

    gross = _decimal(payload.get("gross_realized_result"), "gross_realized_result")
    fee_rate = _decimal(payload.get("administration_fee_rate"), "administration_fee_rate", cents=False)
    fee = _decimal(payload.get("administration_fee"), "administration_fee")
    distributable = _decimal(payload.get("distributable_result"), "distributable_result")
    contribution_base = _decimal(payload.get("total_eligible_contributions"), "total_eligible_contributions")
    if (
        fee != _stored_decimal(review.administration_fee, "review.administration_fee")
        or sum((row.gross_share for row in participants), ZERO) != distributable
        or sum((row.eligible_contributions for row in participants), ZERO) != contribution_base
        or fee_rate != Decimal("0.15")
    ):
        raise _fail("SNAPSHOT_REVIEW_MISMATCH", "snapshot aggregate values do not reconcile")

    liability = _stored_decimal(review.participant_payout_liability, "review.participant_payout_liability")
    actual = _stored_decimal(review.actual_cash_balance, "review.actual_cash_balance")
    ledger = _stored_decimal(review.ledger_cash_balance, "review.ledger_cash_balance")
    difference = _stored_decimal(review.reconciliation_difference, "review.reconciliation_difference", nonnegative=False)
    required = _stored_decimal(review.required_liquidity, "review.required_liquidity")
    surplus = _stored_decimal(review.liquidity_surplus, "review.liquidity_surplus", nonnegative=False)
    if (
        difference != ZERO or difference != actual - ledger
        or required != liability + fee
        or surplus != actual - required or actual < required or surplus < ZERO
        or sum((row.projected_net for row in participants), ZERO) != liability
    ):
        raise _fail("REVIEW_INVALID", "approved review amounts do not reconcile with snapshot")

    raw_gaps = payload.get("source_gaps")
    raw_unavailable = payload.get("unavailable_result_sources")
    if not isinstance(raw_gaps, list) or not isinstance(raw_unavailable, list):
        raise _fail("SNAPSHOT_INVALID", "snapshot source status fields are invalid")
    source_gaps: list[SourceGap] = []
    for item in raw_gaps:
        if not isinstance(item, dict) or not isinstance(item.get("code"), str) or not isinstance(item.get("source"), str):
            raise _fail("SNAPSHOT_INVALID", "snapshot source gap is invalid")
        source_gaps.append(SourceGap(item["code"], item["source"]))
    if any(not isinstance(item, str) for item in raw_unavailable):
        raise _fail("SNAPSHOT_INVALID", "unavailable result source is invalid")

    return _VerifiedSource(
        closing=closing, snapshot=snapshot, review=review, payload=payload,
        participants=participants, source_trace=_trace(payload),
        source_gaps=tuple(sorted(source_gaps, key=lambda item: (item.code, item.source))),
        unavailable_result_sources=tuple(sorted(raw_unavailable)),
        cycle_start_date=_date(payload.get("cycle_start_date"), "cycle_start_date"),
        closing_cutoff_at=cutoff,
    )


def _verify_obligations(db: Session, source: _VerifiedSource) -> bool:
    try:
        rows = verify_cycle_annual_closing_payout_obligations_read_only(
            # The verifier uses only SELECT/no_autoflush; keep its db dependency explicit.
            db, closing_id=source.closing.id,
        )
    except PayoutObligationConflict as exc:
        raise _fail(exc.reason_code or "OBLIGATIONS_INVALID", "payout obligations failed read-only verification") from exc
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise _fail("OBLIGATIONS_INVALID", "payout obligations failed read-only verification") from exc
    total = sum((row.amount for row in rows), ZERO)
    if total != source.review.participant_payout_liability:
        raise _fail("OBLIGATIONS_INVALID", "payout obligations do not match approved liability")
    return True


def _optional_obligation_check(
    db: Session, source: _VerifiedSource, requested: bool,
) -> bool | None:
    return _verify_obligations(db, source) if requested else None


def get_cycle_annual_closing_statement(
    db: Session,
    *,
    closing_id: int,
    verify_payout_obligations: bool = False,
) -> ClosingStatement:
    """Return the official cycle statement from frozen snapshot and review data."""
    with db.no_autoflush:
        source = _load_verified_source(db, closing_id)
        payload = source.payload
        payout_verified = _optional_obligation_check(db, source, verify_payout_obligations)
        review = source.review
        participants = source.participants
        return ClosingStatement(
            closing_id=source.closing.id,
            cycle_id=source.closing.cycle_id,
            snapshot_id=source.snapshot.id,
            snapshot_version=source.snapshot.snapshot_version,
            payload_hash=source.snapshot.payload_hash,
            calculation_version=source.snapshot.calculation_version,
            calculation_hash=payload["calculation_hash"],
            cycle_start_date=source.cycle_start_date,
            closing_cutoff_at=source.closing_cutoff_at,
            financial_timezone=payload["financial_timezone"],
            gross_realized_result=_decimal(payload["gross_realized_result"], "gross_realized_result"),
            administration_fee_rate=_decimal(payload["administration_fee_rate"], "administration_fee_rate", cents=False),
            administration_fee=_decimal(payload["administration_fee"], "administration_fee"),
            distributable_result=_decimal(payload["distributable_result"], "distributable_result"),
            total_eligible_contributions=_decimal(payload["total_eligible_contributions"], "total_eligible_contributions"),
            participant_count=len(participants),
            total_gross_share=sum((row.gross_share for row in participants), ZERO),
            total_gross_entitlement=sum((row.gross_entitlement for row in participants), ZERO),
            total_compensable_obligations=sum((row.compensable_obligations_total for row in participants), ZERO),
            total_compensation=sum((row.projected_compensation for row in participants), ZERO),
            total_projected_net=sum((row.projected_net for row in participants), ZERO),
            total_projected_residual_debt=sum((row.projected_residual_debt for row in participants), ZERO),
            approved_review_id=review.id,
            review_version=review.review_version,
            review_hash=review.review_hash,
            reconciliation_hash=review.reconciliation_hash,
            participant_payout_liability=_stored_decimal(review.participant_payout_liability, "review.participant_payout_liability"),
            ledger_cash_balance=_stored_decimal(review.ledger_cash_balance, "review.ledger_cash_balance"),
            actual_cash_balance=_stored_decimal(review.actual_cash_balance, "review.actual_cash_balance"),
            reconciliation_difference=_stored_decimal(review.reconciliation_difference, "review.reconciliation_difference", nonnegative=False),
            required_liquidity=_stored_decimal(review.required_liquidity, "review.required_liquidity"),
            liquidity_surplus=_stored_decimal(review.liquidity_surplus, "review.liquidity_surplus", nonnegative=False),
            source_gaps=source.source_gaps,
            unavailable_result_sources=source.unavailable_result_sources,
            participants=participants,
            payout_obligations_verified=payout_verified,
        )


def get_cycle_annual_closing_participant_statement(
    db: Session,
    *,
    closing_id: int,
    member_id: int,
    verify_payout_obligations: bool = False,
) -> IndividualClosingStatement:
    """Return one participant's frozen annual closing calculation."""
    if isinstance(member_id, bool) or not isinstance(member_id, int) or member_id <= 0:
        raise _fail("PARTICIPANT_NOT_FOUND", "participant was not found in the closing snapshot")
    with db.no_autoflush:
        source = _load_verified_source(db, closing_id)
        matches = [row for row in source.participants if row.member_id == member_id]
        if len(matches) != 1:
            raise _fail("PARTICIPANT_NOT_FOUND", "participant was not found uniquely in the closing snapshot")
        payout_verified = _optional_obligation_check(db, source, verify_payout_obligations)
        return IndividualClosingStatement(
            closing_id=source.closing.id,
            cycle_id=source.closing.cycle_id,
            snapshot_id=source.snapshot.id,
            snapshot_version=source.snapshot.snapshot_version,
            payload_hash=source.snapshot.payload_hash,
            calculation_version=source.snapshot.calculation_version,
            calculation_hash=source.payload["calculation_hash"],
            cycle_start_date=source.cycle_start_date,
            closing_cutoff_at=source.closing_cutoff_at,
            financial_timezone=source.payload["financial_timezone"],
            approved_review_id=source.review.id,
            review_version=source.review.review_version,
            review_hash=source.review.review_hash,
            reconciliation_hash=source.review.reconciliation_hash,
            participant=matches[0],
            payout_obligations_verified=payout_verified,
        )


def get_cycle_annual_closing_calculation_memory(
    db: Session,
    *,
    closing_id: int,
) -> CalculationMemory:
    """Return the verified calculation memory and source trace without live-source reads."""
    with db.no_autoflush:
        source = _load_verified_source(db, closing_id)
        return CalculationMemory(
            closing_id=source.closing.id,
            cycle_id=source.closing.cycle_id,
            snapshot_id=source.snapshot.id,
            snapshot_version=source.snapshot.snapshot_version,
            payload_hash=source.snapshot.payload_hash,
            calculation_version=source.snapshot.calculation_version,
            calculation_hash=source.payload["calculation_hash"],
            cycle_start_date=source.cycle_start_date,
            closing_cutoff_at=source.closing_cutoff_at,
            financial_timezone=source.payload["financial_timezone"],
            gross_realized_result=_decimal(source.payload["gross_realized_result"], "gross_realized_result"),
            administration_fee_rate=_decimal(source.payload["administration_fee_rate"], "administration_fee_rate", cents=False),
            administration_fee=_decimal(source.payload["administration_fee"], "administration_fee"),
            distributable_result=_decimal(source.payload["distributable_result"], "distributable_result"),
            total_eligible_contributions=_decimal(source.payload["total_eligible_contributions"], "total_eligible_contributions"),
            participants=source.participants,
            source_trace=source.source_trace,
            source_gaps=source.source_gaps,
            unavailable_result_sources=source.unavailable_result_sources,
        )
