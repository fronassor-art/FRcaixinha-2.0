"""Materialize immutable participant obligations from a verified closing snapshot."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    CycleAnnualClosing,
    CycleAnnualClosingPayoutObligation,
    CycleAnnualClosingReview,
    CycleAnnualClosingSnapshot,
    CycleParticipation,
    Member,
)
from app.services.cycle_closing_persistence import verify_cycle_annual_closing_snapshot


CENT = Decimal("0.01")
ZERO = Decimal("0.00")


class PayoutObligationConflict(ValueError):
    """Stored closing or obligation data cannot safely be materialized/reused."""

    def __init__(self, message: str, *, reason_code: str = "OBLIGATIONS_INCOMPLETE"):
        self.reason_code = reason_code
        super().__init__(message)


def _lock_closing(db: Session, closing_id: int) -> CycleAnnualClosing:
    connection = db.connection()
    dialect = connection.dialect.name
    if dialect == "sqlite":
        driver_connection = connection.connection.driver_connection
        if not driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")
        claimed = db.execute(
            update(CycleAnnualClosing)
            .where(CycleAnnualClosing.id == closing_id)
            .values(id=CycleAnnualClosing.id)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            raise PayoutObligationConflict("annual closing process not found")
        closing = db.get(CycleAnnualClosing, closing_id)
    elif dialect == "postgresql":
        closing = db.execute(
            select(CycleAnnualClosing)
            .where(CycleAnnualClosing.id == closing_id)
            .with_for_update()
        ).scalar_one_or_none()
    else:
        raise RuntimeError("Unsupported database dialect for payout obligation locking")
    if closing is None:
        raise PayoutObligationConflict("annual closing process not found")
    return closing


def _decimal_cents(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise PayoutObligationConflict(f"{label} must be a Decimal string")
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise PayoutObligationConflict(f"{label} must be a Decimal string") from exc
    if not amount.is_finite() or amount.quantize(CENT) != amount:
        raise PayoutObligationConflict(f"{label} must be finite and exactly representable in cents")
    if amount < ZERO:
        raise PayoutObligationConflict(f"{label} must be non-negative")
    return amount


def _stored_decimal_cents(value: object, label: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value.quantize(CENT) != value:
        raise PayoutObligationConflict(f"{label} must be finite Decimal cents")
    if value < ZERO:
        raise PayoutObligationConflict(f"{label} must be non-negative")
    return value


def _load_expected_obligations(db: Session, closing: CycleAnnualClosing):
    """Read and validate the immutable snapshot source shared by both paths."""
    if closing.status != "CLOSED":
        raise PayoutObligationConflict(
            "annual closing must be CLOSED before payout obligations are materialized",
            reason_code="CLOSING_NOT_CLOSED",
        )
    if closing.approved_review_id is None:
        raise PayoutObligationConflict(
            "closed annual closing has no approved review",
            reason_code="APPROVED_REVIEW_INVALID",
        )

    snapshot = db.execute(
        select(CycleAnnualClosingSnapshot).where(
            CycleAnnualClosingSnapshot.closing_id == closing.id,
        )
    ).scalar_one_or_none()
    if snapshot is None:
        raise PayoutObligationConflict(
            "official closing snapshot not found", reason_code="SNAPSHOT_MISSING",
        )
    if snapshot.cycle_id != closing.cycle_id:
        raise PayoutObligationConflict(
            "snapshot does not belong to the closing Cycle",
            reason_code="SNAPSHOT_INVALID",
        )
    try:
        verify_cycle_annual_closing_snapshot(snapshot)
        payload = json.loads(
            snapshot.canonical_payload,
            parse_float=lambda _value: (_ for _ in ()).throw(
                PayoutObligationConflict(
                    "float is forbidden in the official snapshot",
                    reason_code="SNAPSHOT_INVALID",
                )
            ),
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PayoutObligationConflict(
            "official closing snapshot failed integrity verification",
            reason_code="SNAPSHOT_INVALID",
        ) from exc
    if not isinstance(payload, dict) or payload.get("cycle_id") != closing.cycle_id:
        raise PayoutObligationConflict(
            "official snapshot does not match the closing Cycle",
            reason_code="SNAPSHOT_INVALID",
        )
    if not isinstance(snapshot.payload_hash, str) or len(snapshot.payload_hash) != 64:
        raise PayoutObligationConflict(
            "official snapshot payload hash is invalid",
            reason_code="SNAPSHOT_INVALID",
        )

    review = db.get(CycleAnnualClosingReview, closing.approved_review_id)
    if (
        review is None
        or review.id != closing.approved_review_id
        or review.closing_id != closing.id
        or review.cycle_id != closing.cycle_id
    ):
        raise PayoutObligationConflict(
            "approved closing review is missing or mismatched",
            reason_code="APPROVED_REVIEW_INVALID",
        )
    liability = _stored_decimal_cents(
        review.participant_payout_liability, "approved payout liability",
    )

    participants = payload.get("participants")
    if not isinstance(participants, list):
        raise PayoutObligationConflict(
            "official snapshot participants are invalid", reason_code="SNAPSHOT_INVALID",
        )
    expected: list[tuple[int, int, Decimal]] = []
    seen_members: set[int] = set()
    seen_participations: set[int] = set()
    for participant in participants:
        if not isinstance(participant, dict):
            raise PayoutObligationConflict(
                "official snapshot participant is invalid", reason_code="SNAPSHOT_INVALID",
            )
        member_id = participant.get("member_id")
        participation_id = participant.get("cycle_participation_id")
        if (
            isinstance(member_id, bool) or not isinstance(member_id, int) or member_id <= 0
            or isinstance(participation_id, bool) or not isinstance(participation_id, int)
            or participation_id <= 0
        ):
            raise PayoutObligationConflict(
                "official snapshot participant identity is invalid",
                reason_code="SNAPSHOT_INVALID",
            )
        if member_id in seen_members or participation_id in seen_participations:
            raise PayoutObligationConflict(
                "official snapshot contains duplicate participant identity",
                reason_code="SNAPSHOT_INVALID",
            )
        seen_members.add(member_id)
        seen_participations.add(participation_id)
        try:
            amount = _decimal_cents(participant.get("projected_net"), "projected_net")
        except PayoutObligationConflict as exc:
            raise PayoutObligationConflict(
                str(exc), reason_code="SNAPSHOT_INVALID",
            ) from exc

        member = db.get(Member, member_id)
        if member is None:
            raise PayoutObligationConflict(
                f"snapshot Member {member_id} does not exist",
                reason_code="SNAPSHOT_INVALID",
            )
        participation = db.get(CycleParticipation, participation_id)
        if participation is None:
            raise PayoutObligationConflict(
                f"snapshot CycleParticipation {participation_id} does not exist",
                reason_code="SNAPSHOT_INVALID",
            )
        if participation.member_id != member_id:
            raise PayoutObligationConflict(
                "snapshot participation belongs to a different Member",
                reason_code="SNAPSHOT_INVALID",
            )
        if participation.cycle_id != closing.cycle_id:
            raise PayoutObligationConflict(
                "snapshot participation belongs to a different Cycle",
                reason_code="SNAPSHOT_INVALID",
            )
        expected.append((member_id, participation_id, amount))

    total = sum((amount for _member_id, _participation_id, amount in expected), ZERO)
    if total != liability:
        raise PayoutObligationConflict(
            "snapshot participant payout total does not equal the approved payout liability",
            reason_code="OBLIGATION_RECONCILIATION_MISMATCH",
        )
    return snapshot, review, payload, expected, liability


def _matches_existing(
    existing: list[CycleAnnualClosingPayoutObligation],
    *,
    expected: list[tuple[int, int, Decimal]],
    snapshot: CycleAnnualClosingSnapshot,
    closing: CycleAnnualClosing,
) -> bool:
    expected_by_identity = {(member_id, participation_id): amount for member_id, participation_id, amount in expected}
    if len(existing) != len(expected_by_identity):
        return False
    seen: set[tuple[int, int]] = set()
    for row in existing:
        identity = (row.member_id, row.cycle_participation_id)
        if (
            identity in seen
            or identity not in expected_by_identity
            or row.snapshot_id != snapshot.id
            or row.closing_id != closing.id
            or row.cycle_id != closing.cycle_id
            or _stored_decimal_cents(row.amount, "stored payout obligation amount")
            != expected_by_identity[identity]
            or row.source_payload_hash != snapshot.payload_hash
        ):
            return False
        seen.add(identity)
    return seen == set(expected_by_identity)


def materialize_cycle_annual_closing_payout_obligations(
    db: Session, *, closing_id: int,
) -> list[CycleAnnualClosingPayoutObligation]:
    """Create or verify all immutable obligations for one CLOSED annual closing.

    The caller owns the transaction and must commit or roll it back. This
    function never changes the closing status/revision and never commits.
    """
    closing = _lock_closing(db, closing_id)
    snapshot, review, _payload, expected, _liability = _load_expected_obligations(db, closing)

    existing = db.execute(
        select(CycleAnnualClosingPayoutObligation)
        .where(CycleAnnualClosingPayoutObligation.snapshot_id == snapshot.id)
        .order_by(CycleAnnualClosingPayoutObligation.id)
    ).scalars().all()
    if existing:
        if not _matches_existing(existing, expected=expected, snapshot=snapshot, closing=closing):
            raise PayoutObligationConflict(
                "existing payout obligation set is partial or differs from the official snapshot"
            )
        return existing

    rows = [
        CycleAnnualClosingPayoutObligation(
            snapshot_id=snapshot.id,
            closing_id=closing.id,
            cycle_id=closing.cycle_id,
            member_id=member_id,
            cycle_participation_id=participation_id,
            amount=amount,
            source_payload_hash=snapshot.payload_hash,
        )
        for member_id, participation_id, amount in expected
    ]
    try:
        with db.begin_nested():
            db.add_all(rows)
            db.flush()
    except IntegrityError as exc:
        reloaded = db.execute(
            select(CycleAnnualClosingPayoutObligation)
            .where(CycleAnnualClosingPayoutObligation.snapshot_id == snapshot.id)
            .order_by(CycleAnnualClosingPayoutObligation.id)
        ).scalars().all()
        if reloaded and _matches_existing(reloaded, expected=expected, snapshot=snapshot, closing=closing):
            return reloaded
        raise PayoutObligationConflict(
            "payout obligation materialization conflicted with an existing or invalid row"
        ) from exc
    return rows


def verify_cycle_annual_closing_payout_obligations_read_only(
    db: Session, *, closing_id: int,
) -> list[CycleAnnualClosingPayoutObligation]:
    """Verify a complete persisted obligation set without locking or writing."""
    with db.no_autoflush:
        closing = db.get(CycleAnnualClosing, closing_id)
        if closing is None:
            raise PayoutObligationConflict(
                "annual closing process not found", reason_code="CLOSING_NOT_FOUND",
            )
        snapshot, _review, _payload, expected, _liability = _load_expected_obligations(
            db, closing,
        )
        existing = db.execute(
            select(CycleAnnualClosingPayoutObligation)
            .where(or_(
                CycleAnnualClosingPayoutObligation.snapshot_id == snapshot.id,
                CycleAnnualClosingPayoutObligation.closing_id == closing.id,
            ))
            .order_by(CycleAnnualClosingPayoutObligation.id)
        ).scalars().all()
        expected_by_identity = {
            (member_id, participation_id): amount
            for member_id, participation_id, amount in expected
        }
        identities = [(row.member_id, row.cycle_participation_id) for row in existing]
        if (
            not existing
            or len(existing) != len(expected_by_identity)
            or len(set(identities)) != len(identities)
            or any(identity not in expected_by_identity for identity in identities)
            or any(
                row.snapshot_id != snapshot.id
                or row.closing_id != closing.id
                or row.cycle_id != closing.cycle_id
                or row.source_payload_hash != snapshot.payload_hash
                for row in existing
            )
        ):
            raise PayoutObligationConflict(
                "existing payout obligation set is partial or differs from the official snapshot",
                reason_code="OBLIGATIONS_INCOMPLETE",
            )
        total = sum(
            (_stored_decimal_cents(row.amount, "stored payout obligation amount") for row in existing),
            ZERO,
        )
        if total != _liability:
            raise PayoutObligationConflict(
                "persisted payout obligations do not equal the approved payout liability",
                reason_code="OBLIGATION_RECONCILIATION_MISMATCH",
            )
        if not _matches_existing(
            existing, expected=expected, snapshot=snapshot, closing=closing,
        ):
            raise PayoutObligationConflict(
                "existing payout obligation set is partial or differs from the official snapshot",
                reason_code="OBLIGATIONS_INCOMPLETE",
            )
        return existing
