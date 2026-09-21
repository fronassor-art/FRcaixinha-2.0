"""Pure deterministic helpers for loan-installment PIX attempts."""

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping
from zoneinfo import ZoneInfo

FINANCIAL_TIMEZONE = "America/Belem"
LOAN_INSTALLMENT_PIX_SNAPSHOT_VERSION = "loan_installment_pix_v1"
CENT = Decimal("0.01")

class ReuseDecision(str, Enum):
    REUSE = "REUSE"
    EXPIRE = "EXPIRE"
    SUPERSEDE = "SUPERSEDE"
    NO_REUSABLE_ATTEMPT = "NO_REUSABLE_ATTEMPT"

@dataclass(frozen=True)
class ReuseEvaluation:
    decision: ReuseDecision

def _financial_zone() -> ZoneInfo:
    return ZoneInfo(FINANCIAL_TIMEZONE)

def financial_date(value: datetime) -> date:
    _require_aware(value, name="value")
    return value.astimezone(_financial_zone()).date()

def local_expiry_utc(financial_day: date) -> datetime:
    next_day = financial_day + timedelta(days=1)
    local_boundary = datetime.combine(next_day, time.min, tzinfo=_financial_zone())
    return local_boundary.astimezone(timezone.utc)

def is_expired(*, now: datetime, expires_at: datetime) -> bool:
    _require_aware(now, name="now")
    _require_aware(expires_at, name="expires_at")
    return now >= expires_at

def normalize_money(value: Decimal) -> str:
    if not isinstance(value, Decimal):
        raise TypeError("money value must be Decimal")
    if not value.is_finite():
        raise ValueError("money value must be finite")
    if value.is_signed() or value < 0:
        raise ValueError("money value cannot be negative")
    if value.as_tuple().exponent < -2:
        raise ValueError("money value must be representable in cents")
    return format(value.quantize(CENT), ".2f")

def build_loan_installment_snapshot(*, loan_id: int | str, installment_id: int | str, installment_number: int, calculated_for_date: date, principal_due: Decimal, normal_price_interest_due: Decimal, fixed_penalty_due: Decimal, late_interest_due: Decimal) -> dict[str, Any]:
    if not isinstance(calculated_for_date, date) or isinstance(calculated_for_date, datetime):
        raise TypeError("calculated_for_date must be date")
    if isinstance(installment_number, bool) or not isinstance(installment_number, int):
        raise TypeError("installment_number must be int")
    if installment_number < 1:
        raise ValueError("installment_number must be positive")
    normalized = {
        "principal_due": normalize_money(principal_due),
        "normal_price_interest_due": normalize_money(normal_price_interest_due),
        "fixed_penalty_due": normalize_money(fixed_penalty_due),
        "late_interest_due": normalize_money(late_interest_due),
    }
    total = sum((Decimal(value) for value in normalized.values()), Decimal("0.00"))
    installment_ref = _serialize_id(installment_id, name="installment_id")
    return {
        "snapshot_version": LOAN_INSTALLMENT_PIX_SNAPSHOT_VERSION,
        "reference_type": "LOAN_INSTALLMENT",
        "reference_id": installment_ref,
        "loan_id": _serialize_id(loan_id, name="loan_id"),
        "installment_id": installment_ref,
        "installment_number": installment_number,
        "calculated_for_date": calculated_for_date.isoformat(),
        "financial_timezone": FINANCIAL_TIMEZONE,
        **normalized,
        "total_due": normalize_money(total),
    }

def canonical_json(snapshot: Mapping[str, Any]) -> str:
    return json.dumps(dict(snapshot), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def snapshot_hash(snapshot: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(snapshot).encode("utf-8")).hexdigest()

def evaluate_reuse(*, attempt_status: str | None, calculated_for_date: date | None, expires_at: datetime | None, stored_snapshot_hash: str | None, current_financial_date: date, now: datetime, current_snapshot_hash: str) -> ReuseEvaluation:
    if not isinstance(current_financial_date, date) or isinstance(current_financial_date, datetime):
        raise TypeError("current_financial_date must be date")
    _require_aware(now, name="now")
    if attempt_status != "PENDING":
        return ReuseEvaluation(ReuseDecision.NO_REUSABLE_ATTEMPT)
    if expires_at is None:
        raise ValueError("PENDING attempt requires expires_at")
    _require_aware(expires_at, name="expires_at")
    if now >= expires_at:
        return ReuseEvaluation(ReuseDecision.EXPIRE)
    if calculated_for_date != current_financial_date or stored_snapshot_hash != current_snapshot_hash:
        return ReuseEvaluation(ReuseDecision.SUPERSEDE)
    return ReuseEvaluation(ReuseDecision.REUSE)

def _require_aware(value: datetime, *, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")

def _serialize_id(value: int | str, *, name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TypeError(f"{name} must be int or str")
    if isinstance(value, str) and not value:
        raise ValueError(f"{name} cannot be empty")
    return str(value)

__all__ = [
    "FINANCIAL_TIMEZONE", "LOAN_INSTALLMENT_PIX_SNAPSHOT_VERSION", "ReuseDecision", "ReuseEvaluation",
    "build_loan_installment_snapshot", "canonical_json", "evaluate_reuse", "financial_date", "is_expired",
    "local_expiry_utc", "normalize_money", "snapshot_hash",
]
