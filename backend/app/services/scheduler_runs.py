"""PostgreSQL-backed scheduler run claims; callers own all commits and rollbacks.

Financial dates passed to this service must be computed as
``financial_civil_date(timezone-aware datetime)`` by the caller. Redis is not
used here. Claim owners should be unique per worker attempt, not a reusable
host name, so a stale claimant cannot acknowledge a later reclaimed lease.
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import SchedulerRun, SchedulerRunUnit


SCHEDULER_FINANCIAL_DATE_SOURCE = "financial_civil_date(timezone-aware datetime)"
RUN_STATUSES = frozenset({"PENDING", "RUNNING", "SUCCEEDED", "FAILED"})
_SAFE_ERROR_CODES = frozenset({
    "execution_failed",
    "lease_lost",
    "dependency_unavailable",
    "database_unavailable",
    "validation_failed",
    "unknown",
})


class SchedulerLeaseLost(RuntimeError):
    pass


def _utc(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scheduler timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    # SQLite may return a naive datetime for a timezone-aware column.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _owner(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 120:
        raise ValueError("lease_owner must be a non-empty identifier of at most 120 characters")
    return value


def _lease_expiry(now: datetime, lease_seconds: int) -> datetime:
    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds <= 0:
        raise ValueError("lease_seconds must be a positive integer")
    return now + timedelta(seconds=lease_seconds)


def _safe_error_code(value: str | None) -> str:
    # Persist only a fixed diagnostic vocabulary; exception text/tracebacks,
    # tokens, payer data, and financial payloads never reach this column.
    return value if value in _SAFE_ERROR_CODES else "execution_failed"


def _for_update(query, db: Session):
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        return query.with_for_update().populate_existing()
    return query


def _get_or_create(db: Session, model, lookup: dict, values: dict):
    row = db.query(model).filter_by(**lookup).one_or_none()
    if row is not None:
        return row, False
    candidate = model(**lookup, **values)
    try:
        # A savepoint confines a uniqueness race without rolling back the
        # caller's transaction or committing hidden work.
        with db.begin_nested():
            db.add(candidate)
            db.flush()
        return candidate, True
    except IntegrityError:
        winner = db.query(model).filter_by(**lookup).one_or_none()
        if winner is None:
            raise
        return winner, False


def get_or_create_run(
    db: Session,
    *,
    job_key: str,
    financial_date: date,
    scheduled_for: datetime,
) -> tuple[SchedulerRun, bool]:
    if not isinstance(job_key, str) or not job_key.strip() or len(job_key) > 100:
        raise ValueError("job_key must be a non-empty identifier of at most 100 characters")
    if isinstance(financial_date, datetime) or not isinstance(financial_date, date):
        raise ValueError("financial_date must be a civil date")
    scheduled_for = _utc(scheduled_for)
    return _get_or_create(
        db,
        SchedulerRun,
        {"job_key": job_key, "financial_date": financial_date},
        {"scheduled_for": scheduled_for, "status": "PENDING", "attempt_count": 0},
    )


def _claim(row, *, lease_owner: str, now: datetime, lease_seconds: int) -> bool:
    owner = _owner(lease_owner)
    expiry = _lease_expiry(now, lease_seconds)
    if row.status == "SUCCEEDED":
        return False
    if row.status == "RUNNING" and row.lease_expires_at is not None:
        if _as_utc(row.lease_expires_at) > now:
            return False
    row.status = "RUNNING"
    row.attempt_count += 1
    row.lease_owner = owner
    row.lease_expires_at = expiry
    row.started_at = now
    row.completed_at = None
    row.last_error = None
    row.updated_at = now
    return True


def claim_run(
    db: Session,
    run_id: int,
    *,
    lease_owner: str,
    now: datetime | None = None,
    lease_seconds: int = 300,
) -> bool:
    now = _utc(now)
    row = _for_update(db.query(SchedulerRun).filter(SchedulerRun.id == run_id), db).one_or_none()
    return False if row is None else _claim(row, lease_owner=lease_owner, now=now, lease_seconds=lease_seconds)


def _assert_claim(row, *, lease_owner: str, now: datetime) -> None:
    if (
        row.status != "RUNNING"
        or row.lease_owner != _owner(lease_owner)
        or row.lease_expires_at is None
        or _as_utc(row.lease_expires_at) <= now
    ):
        raise SchedulerLeaseLost("scheduler lease is no longer owned by this claimant")


def mark_run_succeeded(
    db: Session,
    run_id: int,
    *,
    lease_owner: str,
    completed_at: datetime | None = None,
) -> SchedulerRun:
    now = _utc(completed_at)
    row = _for_update(db.query(SchedulerRun).filter(SchedulerRun.id == run_id), db).one()
    _assert_claim(row, lease_owner=lease_owner, now=now)
    incomplete = db.query(SchedulerRunUnit.id).filter(
        SchedulerRunUnit.run_id == run_id,
        SchedulerRunUnit.status != "SUCCEEDED",
    ).first()
    if incomplete is not None:
        raise ValueError("scheduler run has incomplete units")
    row.status = "SUCCEEDED"
    row.lease_owner = None
    row.lease_expires_at = None
    row.completed_at = now
    row.last_error = None
    row.updated_at = now
    db.flush()
    return row


def lock_run_for_execution(
    db: Session,
    run_id: int,
    *,
    lease_owner: str,
) -> datetime:
    """Validate and lock a claimed run for one atomic execution transaction.

    Callers must keep this Session transaction open through all effects and
    the terminal transition. On PostgreSQL the row lock blocks reclaim until
    that transaction commits or rolls back.
    """
    row = _for_update(db.query(SchedulerRun).filter(SchedulerRun.id == run_id), db).one_or_none()
    if row is None:
        raise SchedulerLeaseLost("scheduler run no longer exists")
    # A transaction may have waited for this row until after its lease
    # expired, so validate using a fresh clock reading after FOR UPDATE.
    validated_at = _utc(datetime.now(timezone.utc))
    _assert_claim(row, lease_owner=lease_owner, now=validated_at)
    return validated_at


def mark_run_succeeded_after_locked_execution(
    db: Session,
    run_id: int,
    *,
    lease_owner: str,
    lease_validated_at: datetime,
    completed_at: datetime | None = None,
) -> SchedulerRun:
    """Complete after lock_run_for_execution in the same open transaction.

    The lease is checked at the instant the row lock was first validated.
    Holding that lock prevents a concurrent reclaim from changing ownership
    while effects run, including when execution exceeds the lease duration.
    """
    validated_at = _utc(lease_validated_at)
    now = _utc(completed_at)
    row = _for_update(db.query(SchedulerRun).filter(SchedulerRun.id == run_id), db).one()
    _assert_claim(row, lease_owner=lease_owner, now=validated_at)
    incomplete = db.query(SchedulerRunUnit.id).filter(
        SchedulerRunUnit.run_id == run_id,
        SchedulerRunUnit.status != "SUCCEEDED",
    ).first()
    if incomplete is not None:
        raise ValueError("scheduler run has incomplete units")
    row.status = "SUCCEEDED"
    row.lease_owner = None
    row.lease_expires_at = None
    row.completed_at = now
    row.last_error = None
    row.updated_at = now
    db.flush()
    return row


def mark_run_failed(
    db: Session,
    run_id: int,
    *,
    lease_owner: str,
    error_code: str | None = None,
    failed_at: datetime | None = None,
) -> SchedulerRun:
    now = _utc(failed_at)
    row = _for_update(db.query(SchedulerRun).filter(SchedulerRun.id == run_id), db).one()
    _assert_claim(row, lease_owner=lease_owner, now=now)
    row.status = "FAILED"
    row.lease_owner = None
    row.lease_expires_at = None
    row.completed_at = None
    row.last_error = _safe_error_code(error_code)
    row.updated_at = now
    db.flush()
    return row


def get_or_create_unit(
    db: Session,
    *,
    run_id: int,
    unit_key: str,
) -> tuple[SchedulerRunUnit, bool]:
    if not isinstance(unit_key, str) or not unit_key.strip() or len(unit_key) > 120:
        raise ValueError("unit_key must be a non-empty identifier of at most 120 characters")
    if db.get(SchedulerRun, run_id) is None:
        raise ValueError("scheduler run does not exist")
    return _get_or_create(
        db,
        SchedulerRunUnit,
        {"run_id": run_id, "unit_key": unit_key},
        {"status": "PENDING", "attempt_count": 0},
    )


def claim_unit(
    db: Session,
    unit_id: int,
    *,
    lease_owner: str,
    now: datetime | None = None,
    lease_seconds: int = 300,
) -> bool:
    now = _utc(now)
    unit = db.get(SchedulerRunUnit, unit_id)
    if unit is None:
        return False
    # Lock parent before unit, matching the run -> unit lock order used by
    # terminal transitions. The caller decides when this transaction commits.
    run = _for_update(db.query(SchedulerRun).filter(SchedulerRun.id == unit.run_id), db).one_or_none()
    if run is None or run.status != "RUNNING" or run.lease_expires_at is None:
        return False
    if _as_utc(run.lease_expires_at) <= now:
        return False
    locked_unit = _for_update(
        db.query(SchedulerRunUnit).filter(SchedulerRunUnit.id == unit_id), db
    ).one_or_none()
    return False if locked_unit is None else _claim(
        locked_unit, lease_owner=lease_owner, now=now, lease_seconds=lease_seconds
    )


def _locked_unit(db: Session, unit_id: int, *, lease_owner: str, now: datetime) -> SchedulerRunUnit:
    unit = db.get(SchedulerRunUnit, unit_id)
    if unit is None:
        raise LookupError("scheduler unit not found")
    # Lock and validate the parent first so unit terminal transitions are
    # fenced by the same active claimant that owns the run.
    run = _for_update(db.query(SchedulerRun).filter(SchedulerRun.id == unit.run_id), db).one()
    row = _for_update(
        db.query(SchedulerRunUnit).filter(SchedulerRunUnit.id == unit_id), db
    ).one()
    _assert_claim(run, lease_owner=lease_owner, now=now)
    _assert_claim(row, lease_owner=lease_owner, now=now)
    return row


def mark_unit_succeeded(
    db: Session,
    unit_id: int,
    *,
    lease_owner: str,
    completed_at: datetime | None = None,
) -> SchedulerRunUnit:
    now = _utc(completed_at)
    row = _locked_unit(db, unit_id, lease_owner=lease_owner, now=now)
    row.status = "SUCCEEDED"
    row.lease_owner = None
    row.lease_expires_at = None
    row.completed_at = now
    row.last_error = None
    row.updated_at = now
    db.flush()
    return row


def mark_unit_failed(
    db: Session,
    unit_id: int,
    *,
    lease_owner: str,
    error_code: str | None = None,
    failed_at: datetime | None = None,
) -> SchedulerRunUnit:
    now = _utc(failed_at)
    row = _locked_unit(db, unit_id, lease_owner=lease_owner, now=now)
    row.status = "FAILED"
    row.lease_owner = None
    row.lease_expires_at = None
    row.completed_at = None
    row.last_error = _safe_error_code(error_code)
    row.updated_at = now
    db.flush()
    return row
