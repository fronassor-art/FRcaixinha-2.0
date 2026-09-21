from datetime import date

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.late_charge_v1 import is_late_charge_eligible


def _settings() -> Settings:
    return Settings(database_url="sqlite://", jwt_secret="test", _env_file=None)


def test_missing_effective_date_is_none(monkeypatch):
    monkeypatch.delenv("LOAN_LATE_CHARGE_EFFECTIVE_DATE", raising=False)

    assert _settings().loan_late_charge_effective_date is None


def test_iso_effective_date_is_parsed_as_civil_date(monkeypatch):
    monkeypatch.setenv("LOAN_LATE_CHARGE_EFFECTIVE_DATE", "2026-12-31")

    assert _settings().loan_late_charge_effective_date == date(2026, 12, 31)


def test_none_effective_date_keeps_rule_inactive():
    due_date = date(2026, 12, 31)

    assert not is_late_charge_eligible(due_date=due_date, effective_date=None)


def test_due_date_before_effective_date_is_ineligible():
    assert not is_late_charge_eligible(
        due_date=date(2026, 12, 30),
        effective_date=date(2026, 12, 31),
    )


def test_due_date_equal_to_effective_date_is_eligible():
    effective_date = date(2026, 12, 31)

    assert is_late_charge_eligible(
        due_date=effective_date,
        effective_date=effective_date,
    )


def test_due_date_after_effective_date_is_eligible():
    assert is_late_charge_eligible(
        due_date=date(2027, 1, 1),
        effective_date=date(2026, 12, 31),
    )


def test_no_active_default_effective_date_exists():
    assert _settings().loan_late_charge_effective_date is None


def test_invalid_effective_date_fails_settings_validation(monkeypatch):
    monkeypatch.setenv("LOAN_LATE_CHARGE_EFFECTIVE_DATE", "not-a-date")

    with pytest.raises(ValidationError):
        _settings()
