"""Transactional domain operations for versioned member PIX destinations."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select, update, func
from sqlalchemy.orm import Session

from app.models import AuditLog, Member, MemberPayoutDestination, User
from app.services.payout_destination_crypto import (
    encrypt_payout_destination,
    mask_payout_destination,
)
from app.services.payout_destination_validation import (
    normalize_key_type,
    normalize_payout_destination,
)


class PayoutDestinationConflict(ValueError):
    """The requested destination transition conflicts with stored state."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lock_member(db: Session, member_id: int) -> Member:
    connection = db.connection()
    if connection.dialect.name == "sqlite":
        driver_connection = connection.connection.driver_connection
        if not driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")
        claimed = db.execute(
            update(Member)
            .where(Member.id == member_id)
            .values(id=Member.id)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            raise PayoutDestinationConflict("Member not found.")
        member = db.get(Member, member_id)
    elif connection.dialect.name == "postgresql":
        member = db.execute(
            select(Member).where(Member.id == member_id).with_for_update()
        ).scalar_one_or_none()
    else:
        raise RuntimeError("Unsupported database dialect for payout destination locking.")
    if member is None:
        raise PayoutDestinationConflict("Member not found.")
    return member


def _require_actor(db: Session, actor_id: int) -> None:
    if db.get(User, actor_id) is None:
        raise PayoutDestinationConflict("Destination actor not found.")


def _active_query(member_id: int):
    return (
        select(MemberPayoutDestination)
        .where(
            MemberPayoutDestination.member_id == member_id,
            MemberPayoutDestination.verification_status != "REVOKED",
        )
        .order_by(MemberPayoutDestination.version.desc())
    )


def get_active_destination(
    db: Session, *, member_id: int,
) -> MemberPayoutDestination | None:
    return db.execute(_active_query(member_id)).scalar_one_or_none()


def _next_version(db: Session, member_id: int) -> int:
    latest = db.scalar(
        select(func.max(MemberPayoutDestination.version)).where(
            MemberPayoutDestination.member_id == member_id
        )
    )
    return (latest or 0) + 1


def _audit(
    db: Session,
    *,
    actor_id: int,
    action: str,
    destination: MemberPayoutDestination,
) -> None:
    safe_details = {
        "member_id": destination.member_id,
        "destination_id": destination.id,
        "key_type": destination.key_type,
        "version": destination.version,
        "masked_value": destination.masked_value,
        "status": destination.verification_status,
    }
    db.add(AuditLog(
        actor_user_id=actor_id,
        action=action,
        entity_type="MEMBER_PAYOUT_DESTINATION",
        entity_id=str(destination.id),
        details=json.dumps(safe_details, sort_keys=True, separators=(",", ":")),
    ))


def _new_destination(
    *,
    member_id: int,
    version: int,
    key_type: str,
    normalized_value: str,
    actor_id: int,
) -> MemberPayoutDestination:
    return MemberPayoutDestination(
        member_id=member_id,
        version=version,
        key_type=key_type,
        encrypted_value=encrypt_payout_destination(normalized_value),
        masked_value=mask_payout_destination(key_type, normalized_value),
        verification_status="UNVERIFIED",
        created_at=_now(),
        created_by=actor_id,
    )


def create_unverified_destination(
    db: Session,
    *,
    member_id: int,
    key_type: str,
    value: str,
    actor_id: int,
) -> MemberPayoutDestination:
    """Create the first active version; caller owns commit/rollback."""
    normalized_type = normalize_key_type(key_type)
    normalized_value = normalize_payout_destination(normalized_type, value)
    _lock_member(db, member_id)
    _require_actor(db, actor_id)
    if db.execute(_active_query(member_id)).scalar_one_or_none() is not None:
        raise PayoutDestinationConflict(
            "Member already has an active payout destination; replace it instead."
        )

    row = _new_destination(
        member_id=member_id,
        version=_next_version(db, member_id),
        key_type=normalized_type,
        normalized_value=normalized_value,
        actor_id=actor_id,
    )
    with db.begin_nested():
        db.add(row)
        db.flush()
        _audit(db, actor_id=actor_id, action="PAYOUT_DESTINATION_CREATED", destination=row)
        db.flush()
    return row


def revoke_destination(
    db: Session,
    *,
    destination_id: int,
    actor_id: int,
) -> MemberPayoutDestination:
    """Revoke an active version without deleting its history."""
    locator = db.get(MemberPayoutDestination, destination_id)
    if locator is None:
        raise PayoutDestinationConflict("Payout destination not found.")
    _lock_member(db, locator.member_id)
    _require_actor(db, actor_id)
    query = select(MemberPayoutDestination).where(
        MemberPayoutDestination.id == destination_id
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    row = db.execute(query).scalar_one_or_none()
    if row is None or row.verification_status == "REVOKED":
        raise PayoutDestinationConflict("Payout destination is not active.")

    with db.begin_nested():
        row.verification_status = "REVOKED"
        row.revoked_at = _now()
        row.revoked_by = actor_id
        db.flush()
        _audit(db, actor_id=actor_id, action="PAYOUT_DESTINATION_REVOKED", destination=row)
        db.flush()
    return row


def replace_destination(
    db: Session,
    *,
    member_id: int,
    key_type: str,
    value: str,
    actor_id: int,
) -> MemberPayoutDestination:
    """Revoke the active version and create its next UNVERIFIED version atomically."""
    normalized_type = normalize_key_type(key_type)
    normalized_value = normalize_payout_destination(normalized_type, value)
    _lock_member(db, member_id)
    _require_actor(db, actor_id)
    current = db.execute(_active_query(member_id)).scalar_one_or_none()
    if current is None:
        raise PayoutDestinationConflict("Member has no active payout destination to replace.")
    next_row = _new_destination(
        member_id=member_id,
        version=_next_version(db, member_id),
        key_type=normalized_type,
        normalized_value=normalized_value,
        actor_id=actor_id,
    )

    with db.begin_nested():
        current.verification_status = "REVOKED"
        current.revoked_at = _now()
        current.revoked_by = actor_id
        db.flush()
        db.add(next_row)
        db.flush()
        _audit(db, actor_id=actor_id, action="PAYOUT_DESTINATION_REVOKED", destination=current)
        _audit(db, actor_id=actor_id, action="PAYOUT_DESTINATION_CREATED", destination=next_row)
        db.flush()
    return next_row
