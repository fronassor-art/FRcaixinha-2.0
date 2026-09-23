"""Versioned, pure civil-day charges for a Contribution."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from app.services.late_charge_v1 import financial_civil_date

RULE_VERSION = "contribution-late-v1"
CENT = Decimal("0.01")
ZERO = Decimal("0.00")
FIXED_PENALTY = Decimal("10.00")
DAILY_RATE = Decimal("0.20") / Decimal("30")


@dataclass(frozen=True)
class ContributionCharges:
    fixed_penalty: Decimal
    daily_interest: Decimal


def calculate_contribution_charges(
    *, principal: Decimal, due_date: date, through: date | datetime,
    principal_payments: tuple[tuple[date, Decimal], ...] = (),
) -> ContributionCharges:
    """Accrue on unpaid principal from the first late day through the civil date.

    A payment reduces the base on its financial date. Interest is rounded once.
    """
    if not isinstance(principal, Decimal) or any(not isinstance(amount, Decimal) for _, amount in principal_payments):
        raise TypeError("financial inputs must be Decimal")
    if principal < ZERO:
        raise ValueError("principal must be nonnegative")
    through_date = financial_civil_date(through)
    balance = principal
    start = due_date + timedelta(days=1)
    exact_interest = Decimal("0")
    late_principal = principal
    for paid_date, amount in sorted(principal_payments, key=lambda item: item[0]):
        if amount < ZERO or amount > balance:
            raise ValueError("invalid principal payment history")
        if paid_date > through_date:
            continue
        if paid_date <= due_date:
            late_principal -= amount
        elif start < paid_date:
            exact_interest += balance * Decimal((paid_date - start).days) * DAILY_RATE
            start = paid_date
        balance -= amount
    if through_date >= start:
        exact_interest += balance * Decimal((through_date - start).days + 1) * DAILY_RATE
    penalty = FIXED_PENALTY if through_date > due_date and late_principal > ZERO else ZERO
    return ContributionCharges(
        fixed_penalty=penalty,
        daily_interest=exact_interest.quantize(CENT, rounding=ROUND_HALF_UP),
    )
