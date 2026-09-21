"""Pure, versioned calculations for the daily-simple late-charge rule.

This module deliberately receives only eligible overdue principal.  It does
not read or write models, and it does not include fixed penalties, normal
installment interest, or previously accrued late interest in its base.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.loan_rules import FINANCIAL_TIMEZONE, LATE_CHARGE_VERSION


CENT = Decimal("0.01")
DAILY_LATE_INTEREST_RATE = Decimal("0.20") / Decimal("30")
FIXED_PENALTY_AMOUNT = Decimal("10.00")

def calculate_fixed_penalty(*, already_assessed: bool) -> Decimal:
    """Return the fixed penalty once; it is never part of the interest base."""
    return Decimal("0.00") if already_assessed else FIXED_PENALTY_AMOUNT


@dataclass(frozen=True)
class PrincipalSegment:
    """Eligible principal and its number of civil late days."""

    principal: Decimal
    days: int


def _decimal(value: Decimal | int | str) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _validate_segment(segment: PrincipalSegment) -> tuple[Decimal, int]:
    principal = _decimal(segment.principal)
    if principal < 0:
        raise ValueError("principal não pode ser negativo")
    if isinstance(segment.days, bool) or not isinstance(segment.days, int):
        raise TypeError("days deve ser inteiro")
    if segment.days < 0:
        raise ValueError("days não pode ser negativo")
    return principal, segment.days


def calculate_late_interest(segments: list[PrincipalSegment] | tuple[PrincipalSegment, ...]) -> Decimal:
    """Return cumulative simple late interest, quantized once at the end.

    Each segment represents an inclusive civil-day count.  Only overdue,
    unpaid principal belongs in the input, so no late interest can accrue on
    a fixed penalty, normal Price interest, or prior late interest.
    """

    exact_total = Decimal("0")
    for segment in segments:
        principal, days = _validate_segment(segment)
        exact_total += principal * Decimal(days) * DAILY_LATE_INTEREST_RATE
    return exact_total.quantize(CENT, rounding=ROUND_HALF_UP)


def financial_civil_date(value: date | datetime) -> date:
    """Return the financial civil date in America/Belem.

    A datetime must be timezone-aware; date values are already civil dates.
    """

    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime deve ser timezone-aware")
        try:
            financial_zone = ZoneInfo(FINANCIAL_TIMEZONE)
        except ZoneInfoNotFoundError:
            financial_zone = timezone(timedelta(hours=-3))
        return value.astimezone(financial_zone).date()
    return value


def is_late_charge_eligible(*, due_date: date, effective_date: date | None) -> bool:
    """Gate the new rule without selecting or inferring an effective date."""

    return effective_date is not None and due_date >= effective_date


__all__ = [
    "CENT",
    "DAILY_LATE_INTEREST_RATE",
    "FIXED_PENALTY_AMOUNT",
    "LATE_CHARGE_VERSION",
    "PrincipalSegment",
    "calculate_late_interest",
    "financial_civil_date",
    "is_late_charge_eligible",
]
