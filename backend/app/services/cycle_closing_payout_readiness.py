"""Strictly read-only readiness evaluation for annual closing payouts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import (
    CycleAnnualClosing,
    CycleAnnualClosingPayoutObligation,
    CycleAnnualClosingReview,
    CycleAnnualClosingSnapshot,
    MemberPayoutDestination,
)
from app.services.cycle_closing_payout_obligations import (
    CENT,
    PayoutObligationConflict,
    verify_cycle_annual_closing_payout_obligations_read_only,
)
from app.services.cycle_closing_persistence import verify_cycle_annual_closing_snapshot
from app.services.cycle_closing_workflow import verify_cycle_annual_closing_review_read_only


ZERO = Decimal("0.00")
REASON_ORDER = (
    "CLOSING_NOT_FOUND",
    "CLOSING_NOT_CLOSED",
    "SNAPSHOT_MISSING",
    "SNAPSHOT_INVALID",
    "APPROVED_REVIEW_INVALID",
    "OBLIGATIONS_INCOMPLETE",
    "OBLIGATION_RECONCILIATION_MISMATCH",
    "INSUFFICIENT_LIQUIDITY",
    "DESTINATION_MISSING",
    "DESTINATION_UNVERIFIED",
    "DESTINATION_REVOKED",
    "DESTINATION_STATE_INVALID",
    "DESTINATION_AMBIGUOUS",
)


@dataclass(frozen=True)
class PayoutReadinessResult:
    ready: bool
    closing_id: int
    cycle_id: int | None
    snapshot_id: int | None
    snapshot_hash: str | None
    approved_review_id: int | None
    review_version: int | None
    participant_payout_liability: Decimal | None
    total_obligation_amount: Decimal | None
    actual_cash_balance: Decimal | None
    required_liquidity: Decimal | None
    liquidity_surplus: Decimal | None
    obligation_count: int
    positive_obligation_count: int
    zero_obligation_count: int
    verified_destination_count: int
    blocked_destination_count: int
    reasons: tuple[str, ...]


def _stored_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("timestamp is invalid")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _decimal_cents(value: object, *, nonnegative: bool = True) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("money must be finite Decimal")
    try:
        if value.quantize(CENT) != value:
            raise ValueError("money must be expressed in cents")
    except InvalidOperation as exc:
        raise ValueError("money must be expressed in cents") from exc
    if nonnegative and value < ZERO:
        raise ValueError("money must be nonnegative")
    return value


def _destination_state(rows: list[MemberPayoutDestination]) -> str:
    """Classify history without decrypting values or trusting unknown states."""
    active = [row for row in rows if row.verification_status != "REVOKED"]
    if len(active) > 1:
        return "AMBIGUOUS"
    if active:
        row = active[0]
        if row.verification_status == "VERIFIED":
            if (
                row.verified_at is not None and row.verified_by is not None
                and row.revoked_at is None and row.revoked_by is None
            ):
                return "VERIFIED"
            return "INVALID"
        if row.verification_status == "UNVERIFIED":
            if (
                row.verified_at is None and row.verified_by is None
                and row.revoked_at is None and row.revoked_by is None
            ):
                return "UNVERIFIED"
            return "INVALID"
        return "INVALID"
    if not rows:
        return "MISSING"
    if all(
        row.verification_status == "REVOKED"
        and row.revoked_at is not None and row.revoked_by is not None
        for row in rows
    ):
        return "REVOKED"
    return "INVALID"


def _review_financial_state(review):
    """Return validated review amounts and sufficiency without database access."""
    liability = _decimal_cents(review.participant_payout_liability)
    actual = _decimal_cents(review.actual_cash_balance)
    fee = _decimal_cents(review.administration_fee)
    difference = _decimal_cents(review.reconciliation_difference, nonnegative=False)
    required = _decimal_cents(review.required_liquidity)
    surplus = _decimal_cents(review.liquidity_surplus, nonnegative=False)
    mathematically_valid = (
        difference == ZERO
        and required == liability + fee
        and surplus == actual - required
    )
    return liability, actual, required, surplus, mathematically_valid, (
        mathematically_valid and actual >= required and surplus >= ZERO
    )


def _empty_result(closing_id: int, reason: str) -> PayoutReadinessResult:
    return PayoutReadinessResult(
        ready=False, closing_id=closing_id, cycle_id=None, snapshot_id=None,
        snapshot_hash=None, approved_review_id=None, review_version=None,
        participant_payout_liability=None, total_obligation_amount=None,
        actual_cash_balance=None, required_liquidity=None, liquidity_surplus=None,
        obligation_count=0, positive_obligation_count=0, zero_obligation_count=0,
        verified_destination_count=0, blocked_destination_count=0,
        reasons=(reason,),
    )


def evaluate_cycle_annual_closing_payout_readiness(
    db: Session, *, closing_id: int,
) -> PayoutReadinessResult:
    """Assess stored payout readiness using SELECTs and pure in-memory checks only.

    This function deliberately does not reverify the stored cash evidence file:
    that verification service appends a WorkflowEvidenceIntegrityEvent. It also
    never invokes the payout-obligation materializer.
    """
    reasons: set[str] = set()
    with db.no_autoflush:
        closing = db.get(CycleAnnualClosing, closing_id)
        if closing is None:
            return _empty_result(closing_id, "CLOSING_NOT_FOUND")

        if closing.status != "CLOSED":
            reasons.add("CLOSING_NOT_CLOSED")

        review: CycleAnnualClosingReview | None = None
        if closing.approved_review_id is not None:
            review = db.get(CycleAnnualClosingReview, closing.approved_review_id)
        review_valid = bool(
            review is not None
            and review.id == closing.approved_review_id
            and review.closing_id == closing.id
            and review.cycle_id == closing.cycle_id
            and closing.approved_at is not None
            and closing.approved_by is not None
            and closing.closed_at is not None
            and closing.closed_by is not None
            and review.process_revision + 2 == closing.state_revision
            and verify_cycle_annual_closing_review_read_only(review)
        )

        liability = actual = required = surplus = None
        liquidity_sufficient = False
        if review_valid:
            try:
                (
                    liability, actual, required, surplus,
                    math_valid, liquidity_sufficient,
                ) = _review_financial_state(review)
                if not math_valid:
                    review_valid = False
            except (InvalidOperation, TypeError, ValueError):
                review_valid = False
        if not review_valid:
            reasons.add("APPROVED_REVIEW_INVALID")
            liability = actual = required = surplus = None
        elif not liquidity_sufficient:
            reasons.add("INSUFFICIENT_LIQUIDITY")

        snapshot = db.execute(
            select(CycleAnnualClosingSnapshot).where(
                CycleAnnualClosingSnapshot.closing_id == closing.id,
            )
        ).scalar_one_or_none()
        snapshot_valid = False
        payload = None
        if snapshot is None:
            reasons.add("SNAPSHOT_MISSING")
        else:
            try:
                verify_cycle_annual_closing_snapshot(snapshot)
                payload = json.loads(
                    snapshot.canonical_payload,
                    parse_float=lambda _value: (_ for _ in ()).throw(
                        ValueError("float in snapshot")
                    ),
                )
                snapshot_valid = (
                    snapshot.id > 0
                    and snapshot.closing_id == closing.id
                    and snapshot.cycle_id == closing.cycle_id
                    and snapshot.created_by == closing.closed_by
                    and isinstance(payload, dict)
                    and payload.get("cycle_id") == closing.cycle_id
                )
                if review_valid:
                    snapshot_valid = snapshot_valid and (
                        _stored_utc(snapshot.closing_cutoff_at)
                        == _stored_utc(review.closing_cutoff_at)
                        and snapshot.calculation_version == review.calculation_version
                        and payload.get("calculation_version") == review.calculation_version
                        and payload.get("calculation_hash") == review.calculation_hash
                    )
            except (TypeError, ValueError, json.JSONDecodeError, AttributeError):
                snapshot_valid = False
            if not snapshot_valid:
                reasons.add("SNAPSHOT_INVALID")

        raw_obligations: list[CycleAnnualClosingPayoutObligation] = []
        if snapshot is not None:
            raw_obligations = db.execute(
                select(CycleAnnualClosingPayoutObligation)
                .where(or_(
                    CycleAnnualClosingPayoutObligation.snapshot_id == snapshot.id,
                    CycleAnnualClosingPayoutObligation.closing_id == closing.id,
                ))
                .order_by(CycleAnnualClosingPayoutObligation.id)
            ).scalars().all()
        elif closing.id is not None:
            raw_obligations = db.execute(
                select(CycleAnnualClosingPayoutObligation)
                .where(CycleAnnualClosingPayoutObligation.closing_id == closing.id)
                .order_by(CycleAnnualClosingPayoutObligation.id)
            ).scalars().all()

        total_obligation_amount: Decimal | None = ZERO
        try:
            stored_amounts = [
                _decimal_cents(row.amount) for row in raw_obligations
            ]
            total_obligation_amount = sum(stored_amounts, ZERO)
        except (InvalidOperation, TypeError, ValueError):
            total_obligation_amount = None
            reasons.add("OBLIGATIONS_INCOMPLETE")

        obligation_count = len(raw_obligations)
        positive_count = sum(
            1 for row in raw_obligations
            if isinstance(row.amount, Decimal) and row.amount > ZERO
        )
        zero_count = sum(
            1 for row in raw_obligations
            if isinstance(row.amount, Decimal) and row.amount == ZERO
        )

        verified_destinations = 0
        blocked_destinations = 0
        obligations_valid = False
        if snapshot_valid and review_valid and closing.status == "CLOSED":
            try:
                verified_rows = verify_cycle_annual_closing_payout_obligations_read_only(
                    db, closing_id=closing.id,
                )
                obligations_valid = True
                obligation_count = len(verified_rows)
                values = [_decimal_cents(row.amount) for row in verified_rows]
                total_obligation_amount = sum(values, ZERO)
                positive_rows = [row for row in verified_rows if row.amount > ZERO]
                positive_count = len(positive_rows)
                zero_count = sum(1 for row in verified_rows if row.amount == ZERO)
                if total_obligation_amount != liability:
                    reasons.add("OBLIGATION_RECONCILIATION_MISMATCH")

                positive_member_ids = {row.member_id for row in positive_rows}
                destination_rows = db.execute(
                    select(MemberPayoutDestination)
                    .where(MemberPayoutDestination.member_id.in_(positive_member_ids))
                    .order_by(MemberPayoutDestination.member_id, MemberPayoutDestination.version)
                ).scalars().all() if positive_member_ids else []
                by_member: dict[int, list[MemberPayoutDestination]] = {}
                for destination in destination_rows:
                    by_member.setdefault(destination.member_id, []).append(destination)
                for member_id in sorted(positive_member_ids):
                    state = _destination_state(by_member.get(member_id, []))
                    if state == "VERIFIED":
                        verified_destinations += 1
                    else:
                        blocked_destinations += 1
                        reasons.add({
                            "MISSING": "DESTINATION_MISSING",
                            "UNVERIFIED": "DESTINATION_UNVERIFIED",
                            "REVOKED": "DESTINATION_REVOKED",
                            "INVALID": "DESTINATION_STATE_INVALID",
                            "AMBIGUOUS": "DESTINATION_AMBIGUOUS",
                        }[state])
            except PayoutObligationConflict as exc:
                reason = exc.reason_code
                if reason not in REASON_ORDER:
                    reason = "OBLIGATIONS_INCOMPLETE"
                reasons.add(reason)
            except (InvalidOperation, TypeError, ValueError):
                reasons.add("OBLIGATIONS_INCOMPLETE")
        else:
            reasons.add("OBLIGATIONS_INCOMPLETE")

        if obligations_valid and liability is not None and total_obligation_amount is not None:
            if total_obligation_amount != liability:
                reasons.add("OBLIGATION_RECONCILIATION_MISMATCH")

        ordered_reasons = tuple(code for code in REASON_ORDER if code in reasons)
        return PayoutReadinessResult(
            ready=not ordered_reasons,
            closing_id=closing.id,
            cycle_id=closing.cycle_id,
            snapshot_id=snapshot.id if snapshot is not None else None,
            snapshot_hash=snapshot.payload_hash if snapshot is not None else None,
            approved_review_id=review.id if review_valid else None,
            review_version=review.review_version if review_valid else None,
            participant_payout_liability=liability,
            total_obligation_amount=total_obligation_amount,
            actual_cash_balance=actual,
            required_liquidity=required,
            liquidity_surplus=surplus,
            obligation_count=obligation_count,
            positive_obligation_count=positive_count,
            zero_obligation_count=zero_count,
            verified_destination_count=verified_destinations,
            blocked_destination_count=blocked_destinations,
            reasons=ordered_reasons,
        )
