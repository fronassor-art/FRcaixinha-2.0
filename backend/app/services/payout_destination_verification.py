"""Transactional persistence operations for payout verification evidence.

This service stores sanitized provider-neutral results only. It never reads or
decrypts the destination key, calls a provider, commits the caller's
transaction, or changes a destination's verification status.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.payout_destination_verification import (
    DestinationFreshness,
    VerificationResult,
    VerificationResultState,
    evaluate_destination_freshness,
)
from app.models import (
    Member,
    MemberPayoutDestination,
    PayoutDestinationVerificationAttempt,
    PayoutDestinationVerificationEvidence,
    User,
)


EVIDENCE_SCHEMA_VERSION = "payout_verification_evidence_v1"
_AUTHENTICITY_STATUSES = frozenset(
    {"NOT_CHECKED", "AUTHENTIC", "INVALID", "UNVERIFIABLE"}
)
_SAFE_REASON_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")


class PayoutVerificationConflict(ValueError):
    """A safe, non-sensitive verification operation conflict."""


class PayoutVerificationNotFound(LookupError):
    """A required destination or verification attempt was not found."""


class PayoutVerificationIntegrityError(RuntimeError):
    """Persistence failed without exposing a driver or constraint message."""


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PayoutVerificationConflict(f"{field_name} must be timezone-aware.")
    return value.astimezone(timezone.utc)


def _db_datetime_utc(value: datetime | None) -> datetime | None:
    """SQLite drops timezone metadata; persisted naive timestamps are UTC."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime | None) -> str | None:
    normalized = _db_datetime_utc(value)
    if normalized is None:
        return None
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _require_text(value: str, *, maximum: int, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise PayoutVerificationConflict(f"{field_name} is invalid.")
    return value


def _positive_id(value: int, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PayoutVerificationConflict(f"{field_name} is invalid.")
    return value


def _lock_member(db: Session, member_id: int) -> None:
    """Serialize payout destination work using the project's SQLite/PG policy."""
    connection = db.connection()
    dialect = connection.dialect.name
    if dialect == "sqlite":
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
            raise PayoutVerificationNotFound("Payout destination member was not found.")
        return
    if dialect == "postgresql":
        locked_id = db.execute(
            select(Member.id).where(Member.id == member_id).with_for_update()
        ).scalar_one_or_none()
        if locked_id is None:
            raise PayoutVerificationNotFound("Payout destination member was not found.")
        return
    raise PayoutVerificationIntegrityError("Database dialect is not supported.")


def _destination_projection(db: Session, destination_id: int):
    """Read identity/status columns only; never select encrypted_value."""
    return db.execute(
        select(
            MemberPayoutDestination.id,
            MemberPayoutDestination.member_id,
            MemberPayoutDestination.version,
            MemberPayoutDestination.key_type,
            MemberPayoutDestination.verification_status,
        ).where(MemberPayoutDestination.id == destination_id)
    ).one_or_none()


def _active_destination(db: Session, member_id: int, *, lock: bool = False):
    statement = (
        select(
            MemberPayoutDestination.id,
            MemberPayoutDestination.member_id,
            MemberPayoutDestination.version,
            MemberPayoutDestination.key_type,
            MemberPayoutDestination.verification_status,
        )
        .where(
            MemberPayoutDestination.member_id == member_id,
            MemberPayoutDestination.verification_status != "REVOKED",
        )
        .order_by(MemberPayoutDestination.version.desc())
    )
    if lock and db.bind is not None and db.bind.dialect.name == "postgresql":
        statement = statement.with_for_update()
    rows = db.execute(statement).all()
    if len(rows) > 1:
        raise PayoutVerificationIntegrityError("Member has inconsistent active destinations.")
    return rows[0] if rows else None


def _attempt_by_idempotency(db: Session, idempotency_key: str):
    return db.execute(
        select(PayoutDestinationVerificationAttempt)
        .where(PayoutDestinationVerificationAttempt.idempotency_key == idempotency_key)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _compatible_existing_start(
    db: Session,
    attempt: PayoutDestinationVerificationAttempt,
    *,
    destination_id: int,
    provider_name: str,
) -> PayoutDestinationVerificationAttempt:
    destination = _destination_projection(db, destination_id)
    if (
        attempt.destination_id != destination_id
        or attempt.provider_name != provider_name
        or destination is None
        or attempt.destination_version != destination.version
        or attempt.key_type != destination.key_type
    ):
        raise PayoutVerificationConflict("Idempotency key conflicts with an existing attempt.")
    return attempt


def _active_attempt(db: Session, destination_id: int):
    return db.execute(
        select(PayoutDestinationVerificationAttempt)
        .where(
            PayoutDestinationVerificationAttempt.destination_id == destination_id,
            PayoutDestinationVerificationAttempt.state.in_(("REQUESTED", "PENDING")),
        )
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def start_verification_attempt(
    db: Session,
    *,
    destination_id: int,
    idempotency_key: str,
    provider_name: str,
    requested_by: int | None,
    requested_at: datetime | None = None,
) -> PayoutDestinationVerificationAttempt:
    """Start or idempotently reuse an attempt; caller owns commit/rollback."""
    destination_id = _positive_id(destination_id, field_name="destination_id")
    idempotency_key = _require_text(idempotency_key, maximum=150, field_name="idempotency_key")
    provider_name = _require_text(provider_name, maximum=80, field_name="provider_name")
    if requested_by is not None:
        requested_by = _positive_id(requested_by, field_name="requested_by")
    requested_at = _aware_utc(requested_at or _now_utc(), field_name="requested_at")

    existing = _attempt_by_idempotency(db, idempotency_key)
    if existing is not None:
        return _compatible_existing_start(
            db, existing, destination_id=destination_id, provider_name=provider_name
        )

    locator = _destination_projection(db, destination_id)
    if locator is None:
        raise PayoutVerificationNotFound("Payout destination was not found.")
    _lock_member(db, locator.member_id)

    # Recheck after serialization: a concurrent caller may have won the key.
    existing = _attempt_by_idempotency(db, idempotency_key)
    if existing is not None:
        return _compatible_existing_start(
            db, existing, destination_id=destination_id, provider_name=provider_name
        )

    active = _active_destination(db, locator.member_id, lock=True)
    if active is None or active.id != destination_id:
        raise PayoutVerificationConflict("Payout destination is not the active version.")
    if (
        active.version != locator.version
        or active.key_type != locator.key_type
        or active.verification_status != "UNVERIFIED"
    ):
        raise PayoutVerificationConflict("Payout destination is not eligible for a new attempt.")

    if requested_by is not None and db.scalar(
        select(User.id).where(User.id == requested_by)
    ) is None:
        raise PayoutVerificationNotFound("Requesting actor was not found.")

    if _active_attempt(db, destination_id) is not None:
        raise PayoutVerificationConflict("Payout destination already has an active attempt.")

    row = PayoutDestinationVerificationAttempt(
        attempt_id=secrets.token_hex(16),
        idempotency_key=idempotency_key,
        destination_id=destination_id,
        destination_version=locator.version,
        key_type=locator.key_type,
        provider_name=provider_name,
        state="REQUESTED",
        requested_at=requested_at,
        requested_by=requested_by,
        created_at=_now_utc(),
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        winner = _attempt_by_idempotency(db, idempotency_key)
        if winner is not None:
            return _compatible_existing_start(
                db, winner, destination_id=destination_id, provider_name=provider_name
            )
        if _active_attempt(db, destination_id) is not None:
            raise PayoutVerificationConflict(
                "Payout destination already has an active attempt."
            ) from None
        raise PayoutVerificationIntegrityError(
            "Verification attempt could not be persisted."
        ) from None
    return row


def _validate_authenticity(
    status: str,
    method: str | None,
    checked_at: datetime | None,
) -> tuple[str, str | None, datetime | None]:
    if not isinstance(status, str) or status not in _AUTHENTICITY_STATUSES:
        raise PayoutVerificationConflict("Authenticity status is invalid.")
    if status == "NOT_CHECKED":
        if method is not None or checked_at is not None:
            raise PayoutVerificationConflict("Authenticity metadata is invalid.")
        return status, None, None
    method = _require_text(method, maximum=80, field_name="authenticity_method")
    if checked_at is None:
        raise PayoutVerificationConflict("Authenticity metadata is invalid.")
    return status, method, _aware_utc(checked_at, field_name="authenticity_checked_at")


def _freshness_for_attempt(db: Session, attempt, member_id: int, destination):
    current = _active_destination(db, member_id, lock=True)
    if current is None:
        if destination.verification_status == "REVOKED":
            current_id = attempt.destination_id
            current_version = attempt.destination_version
            current_status = "REVOKED"
        else:
            raise PayoutVerificationIntegrityError("Destination state is inconsistent.")
    else:
        current_id = current.id
        current_version = current.version
        current_status = current.verification_status
    return evaluate_destination_freshness(
        _attempt_domain_value(attempt),
        current_destination_id=current_id,
        current_destination_version=current_version,
        current_status=current_status,
    )


def _attempt_domain_value(attempt):
    # Freshness needs only these safe identity fields; no destination secret is read.
    from app.core.payout_destination_verification import VerificationAttempt

    return VerificationAttempt(
        attempt_id=attempt.attempt_id,
        idempotency_key=attempt.idempotency_key,
        destination_id=attempt.destination_id,
        destination_version=attempt.destination_version,
        key_type=attempt.key_type,
        normalized_key="not-loaded",
        requested_at=_db_datetime_utc(attempt.requested_at),
    )


def canonical_evidence_payload(
    attempt,
    result: VerificationResult,
    *,
    authenticity_status: str,
    authenticity_method: str | None,
    authenticity_checked_at: datetime | None,
    freshness: DestinationFreshness,
) -> str:
    """Build the stable sanitized JSON used as the evidence digest input."""
    payload = {
        "attempt_id": attempt.attempt_id,
        "authenticity_checked_at": _timestamp(authenticity_checked_at),
        "authenticity_method": authenticity_method,
        "authenticity_status": authenticity_status,
        "destination_id": attempt.destination_id,
        "destination_version": attempt.destination_version,
        "freshness": freshness.value,
        "outcome": result.outcome.value if result.outcome is not None else None,
        "provider_event_id": result.provider_event_id,
        "provider_name": result.provider_name,
        "provider_request_id": result.provider_request_id,
        "provider_response_id": result.provider_response_id,
        "provider_timestamp": _timestamp(result.provider_timestamp),
        "received_at": _timestamp(result.received_at),
        "reason_code": result.reason_code,
        "result_state": result.state.value,
        "schema_version": EVIDENCE_SCHEMA_VERSION,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_evidence_digest(
    attempt,
    result: VerificationResult,
    *,
    authenticity_status: str,
    authenticity_method: str | None,
    authenticity_checked_at: datetime | None,
    freshness: DestinationFreshness,
) -> str:
    payload = canonical_evidence_payload(
        attempt,
        result,
        authenticity_status=authenticity_status,
        authenticity_method=authenticity_method,
        authenticity_checked_at=authenticity_checked_at,
        freshness=freshness,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _evidence_matches(
    evidence,
    *,
    attempt,
    result,
    authenticity_status,
    authenticity_method,
    authenticity_checked_at,
    freshness,
    digest,
) -> bool:
    expected = {
        "verification_attempt_id": attempt.id,
        "result_state": result.state.value,
        "outcome": result.outcome.value if result.outcome is not None else None,
        "reason_code": result.reason_code,
        "provider_request_id": result.provider_request_id,
        "provider_response_id": result.provider_response_id,
        "provider_event_id": result.provider_event_id,
        "provider_timestamp": _timestamp(result.provider_timestamp),
        "received_at": _timestamp(result.received_at),
        "authenticity_status": authenticity_status,
        "authenticity_method": authenticity_method,
        "authenticity_checked_at": _timestamp(authenticity_checked_at),
        "freshness": freshness.value,
        "evidence_digest": digest,
    }
    for field, value in expected.items():
        actual = getattr(evidence, field)
        if field in {"provider_timestamp", "received_at", "authenticity_checked_at"}:
            actual = _timestamp(actual)
        if actual != value:
            return False
    return True


def _advance_attempt(db: Session, attempt, result_state: str) -> None:
    if result_state == "PENDING":
        if attempt.state == "REQUESTED":
            attempt.state = "PENDING"
            db.flush()
            return
        if attempt.state == "PENDING":
            return
        raise PayoutVerificationConflict("Verification attempt is already final.")
    if result_state == "FINAL":
        if attempt.state in {"REQUESTED", "PENDING"}:
            attempt.state = "FINAL"
            db.flush()
            return
        if attempt.state == "FINAL":
            return
    raise PayoutVerificationConflict("Verification attempt state is inconsistent.")


def record_verification_result(
    db: Session,
    *,
    result: VerificationResult,
    authenticity_status: str,
    authenticity_method: str | None = None,
    authenticity_checked_at: datetime | None = None,
) -> PayoutDestinationVerificationEvidence:
    """Persist a sanitized result and matching attempt transition atomically.

    This function uses a savepoint for its evidence/state unit but never commits
    or rolls back the caller's outer transaction.
    """
    if not isinstance(result, VerificationResult):
        raise PayoutVerificationConflict("Verification result is invalid.")
    for field_name in ("provider_request_id", "provider_response_id", "provider_event_id"):
        provider_id = getattr(result, field_name)
        if provider_id is not None:
            _require_text(provider_id, maximum=255, field_name=field_name)
    auth_status, auth_method, auth_checked = _validate_authenticity(
        authenticity_status, authenticity_method, authenticity_checked_at
    )
    if result.reason_code is not None and not _SAFE_REASON_CODE.fullmatch(result.reason_code):
        raise PayoutVerificationConflict("Verification reason code is invalid.")

    attempt_locator = db.execute(
        select(
            PayoutDestinationVerificationAttempt.destination_id,
            PayoutDestinationVerificationAttempt.attempt_id,
        ).where(PayoutDestinationVerificationAttempt.attempt_id == result.attempt_id)
    ).one_or_none()
    if attempt_locator is None:
        raise PayoutVerificationNotFound("Verification attempt was not found.")
    destination = _destination_projection(db, attempt_locator.destination_id)
    if destination is None:
        raise PayoutVerificationIntegrityError("Verification destination binding is invalid.")
    _lock_member(db, destination.member_id)

    statement = select(PayoutDestinationVerificationAttempt).where(
        PayoutDestinationVerificationAttempt.attempt_id == result.attempt_id
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        statement = statement.with_for_update()
    attempt = db.execute(statement.execution_options(populate_existing=True)).scalar_one_or_none()
    if attempt is None:
        raise PayoutVerificationNotFound("Verification attempt was not found.")
    if (
        attempt.destination_id != destination.id
        or attempt.destination_version != destination.version
        or attempt.key_type != destination.key_type
    ):
        raise PayoutVerificationIntegrityError("Verification destination binding is invalid.")
    if result.provider_name != attempt.provider_name:
        raise PayoutVerificationConflict("Provider does not match the verification attempt.")

    freshness = _freshness_for_attempt(db, attempt, destination.member_id, destination)
    digest = compute_evidence_digest(
        attempt,
        result,
        authenticity_status=auth_status,
        authenticity_method=auth_method,
        authenticity_checked_at=auth_checked,
        freshness=freshness,
    )
    if result.evidence_digest is not None and result.evidence_digest != digest:
        raise PayoutVerificationConflict("Evidence digest does not match the sanitized result.")

    existing = db.execute(
        select(PayoutDestinationVerificationEvidence)
        .where(
            PayoutDestinationVerificationEvidence.verification_attempt_id == attempt.id,
            PayoutDestinationVerificationEvidence.evidence_digest == digest,
        )
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if existing is not None:
        if not _evidence_matches(
            existing,
            attempt=attempt,
            result=result,
            authenticity_status=auth_status,
            authenticity_method=auth_method,
            authenticity_checked_at=auth_checked,
            freshness=freshness,
            digest=digest,
        ):
            raise PayoutVerificationIntegrityError("Stored evidence conflicts with its digest.")
        with db.begin_nested():
            _advance_attempt(db, attempt, result.state.value)
        return existing

    if attempt.state == "FINAL":
        raise PayoutVerificationConflict("Verification attempt is already final.")
    final_evidence = db.execute(
        select(PayoutDestinationVerificationEvidence.id).where(
            PayoutDestinationVerificationEvidence.verification_attempt_id == attempt.id,
            PayoutDestinationVerificationEvidence.result_state == "FINAL",
        )
    ).scalar_one_or_none()
    if final_evidence is not None:
        raise PayoutVerificationConflict("Verification attempt already has final evidence.")

    next_sequence = (
        db.scalar(
            select(func.max(PayoutDestinationVerificationEvidence.sequence)).where(
                PayoutDestinationVerificationEvidence.verification_attempt_id == attempt.id
            )
        )
        or 0
    ) + 1
    evidence = PayoutDestinationVerificationEvidence(
        verification_attempt_id=attempt.id,
        sequence=next_sequence,
        result_state=result.state.value,
        outcome=result.outcome.value if result.outcome is not None else None,
        reason_code=result.reason_code,
        provider_request_id=result.provider_request_id,
        provider_response_id=result.provider_response_id,
        provider_event_id=result.provider_event_id,
        provider_timestamp=_db_datetime_utc(result.provider_timestamp),
        received_at=_aware_utc(result.received_at, field_name="received_at"),
        authenticity_status=auth_status,
        authenticity_method=auth_method,
        authenticity_checked_at=auth_checked,
        freshness=freshness.value,
        evidence_digest=digest,
        created_at=_now_utc(),
    )
    try:
        with db.begin_nested():
            db.add(evidence)
            db.flush()
            _advance_attempt(db, attempt, result.state.value)
    except IntegrityError:
        existing = db.execute(
            select(PayoutDestinationVerificationEvidence)
            .where(
                PayoutDestinationVerificationEvidence.verification_attempt_id == attempt.id,
                PayoutDestinationVerificationEvidence.evidence_digest == digest,
            )
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if existing is not None and _evidence_matches(
            existing,
            attempt=attempt,
            result=result,
            authenticity_status=auth_status,
            authenticity_method=auth_method,
            authenticity_checked_at=auth_checked,
            freshness=freshness,
            digest=digest,
        ):
            with db.begin_nested():
                _advance_attempt(db, attempt, result.state.value)
            return existing
        raise PayoutVerificationConflict("Verification result conflicts with stored state.") from None
    return evidence


__all__ = [
    "EVIDENCE_SCHEMA_VERSION",
    "PayoutVerificationConflict",
    "PayoutVerificationIntegrityError",
    "PayoutVerificationNotFound",
    "canonical_evidence_payload",
    "compute_evidence_digest",
    "record_verification_result",
    "start_verification_attempt",
]
