from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.services.late_charge_v1 import (
    FIXED_PENALTY_AMOUNT,
    PrincipalSegment,
    calculate_late_interest,
    financial_civil_date,
    is_late_charge_eligible,
)


def segment(principal: str, days: int) -> PrincipalSegment:
    return PrincipalSegment(Decimal(principal), days)


def test_100_for_30_days_is_20_not_daily_rounded_20_10():
    assert calculate_late_interest([segment("100.00", 30)]) == Decimal("20.00")


def test_zero_days_and_first_day_boundaries_are_explicit_counts():
    assert calculate_late_interest([segment("100.00", 0)]) == Decimal("0.00")
    assert calculate_late_interest([segment("100.00", 1)]) == Decimal("0.67")


def test_15_11_for_10_days_rounds_only_the_cumulative_total():
    assert calculate_late_interest([segment("15.11", 10)]) == Decimal("1.01")


def test_payment_segmentation_100_for_5_then_60_for_5():
    assert calculate_late_interest([segment("100.00", 5), segment("60.00", 5)]) == Decimal("5.33")


def test_prospective_reversal_100_for_5_then_60_for_7():
    assert calculate_late_interest([segment("100.00", 5), segment("60.00", 7)]) == Decimal("6.13")


def test_equivalent_frequency_or_catch_up_has_same_result():
    once = calculate_late_interest([segment("100.00", 10)])
    daily_segments = calculate_late_interest([segment("100.00", 1) for _ in range(10)])
    catch_up_segments = calculate_late_interest([segment("100.00", 5), segment("100.00", 5)])
    assert once == daily_segments == catch_up_segments


def test_fixed_penalty_is_not_a_late_interest_base():
    assert FIXED_PENALTY_AMOUNT == Decimal("10.00")
    from app.services.late_charge_v1 import calculate_fixed_penalty
    assert calculate_fixed_penalty(already_assessed=False) == Decimal("10.00")
    assert calculate_fixed_penalty(already_assessed=True) == Decimal("0.00")
    assert calculate_late_interest([segment("100.00", 30)]) == Decimal("20.00")


def test_price_interest_and_previous_mora_are_not_inputs_to_the_base():
    assert calculate_late_interest([segment("100.00", 30)]) == Decimal("20.00")
    assert calculate_late_interest([segment("120.00", 30)]) == Decimal("24.00")
    assert calculate_late_interest([segment("100.00", 30)]) != Decimal("22.00")


def test_zero_principal_and_rejected_negative_values():
    assert calculate_late_interest([segment("0.00", 30)]) == Decimal("0.00")
    with pytest.raises(ValueError):
        calculate_late_interest([segment("-0.01", 1)])
    with pytest.raises(ValueError):
        calculate_late_interest([segment("1.00", -1)])


def test_effective_date_gate_is_inactive_until_explicitly_configured():
    due = date(2026, 1, 10)
    assert not is_late_charge_eligible(due_date=due, effective_date=None)
    assert not is_late_charge_eligible(due_date=due, effective_date=date(2026, 1, 11))
    assert is_late_charge_eligible(due_date=due, effective_date=due)
    assert is_late_charge_eligible(due_date=due, effective_date=date(2026, 1, 9))


def test_belem_civil_date_at_utc_boundary():
    assert financial_civil_date(datetime(2026, 1, 11, 2, 59, tzinfo=timezone.utc)) == date(2026, 1, 10)
    assert financial_civil_date(datetime(2026, 1, 11, 3, 0, tzinfo=timezone.utc)) == date(2026, 1, 11)


def test_legacy_fields_and_engine_remain_available():
    from app.services.loan_engine_v17 import accrue_overdue_penalties, calculate_daily_penalty

    assert callable(calculate_daily_penalty)
    assert callable(accrue_overdue_penalties)
