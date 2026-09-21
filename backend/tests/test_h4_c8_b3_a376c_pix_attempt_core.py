from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from zoneinfo import ZoneInfoNotFoundError

from app.core.pix_attempt_v1 import (
    FINANCIAL_TIMEZONE,
    LOAN_INSTALLMENT_PIX_SNAPSHOT_VERSION,
    ReuseDecision,
    build_loan_installment_snapshot,
    canonical_json,
    evaluate_reuse,
    financial_date,
    is_expired,
    local_expiry_utc,
    normalize_money,
    snapshot_hash,
)


def _snapshot(**overrides):
    values = {
        "loan_id": 45,
        "installment_id": 123,
        "installment_number": 3,
        "calculated_for_date": date(2026, 9, 21),
        "principal_due": Decimal("100.00"),
        "normal_price_interest_due": Decimal("20.00"),
        "fixed_penalty_due": Decimal("0.00"),
        "late_interest_due": Decimal("0.00"),
    }
    values.update(overrides)
    return build_loan_installment_snapshot(**values)


def _now(value="2026-09-21T12:00:00+00:00"):
    return datetime.fromisoformat(value)


def test_financial_date_converts_utc_to_belem_before_midnight():
    assert financial_date(datetime(2026, 9, 22, 2, 59, tzinfo=timezone.utc)) == date(2026, 9, 21)


def test_financial_date_handles_belem_midnight_boundary():
    assert financial_date(datetime(2026, 9, 22, 3, 0, tzinfo=timezone.utc)) == date(2026, 9, 22)
    assert financial_date(datetime(2026, 9, 22, 3, 1, tzinfo=timezone.utc)) == date(2026, 9, 22)


def test_financial_date_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        financial_date(datetime(2026, 9, 21, 12, 0))


def test_financial_timezone_fails_closed_when_zoneinfo_is_unavailable(monkeypatch):
    import app.core.pix_attempt_v1 as pix_attempt

    def unavailable_zone(_name):
        raise ZoneInfoNotFoundError("America/Belem unavailable")

    monkeypatch.setattr(pix_attempt, "ZoneInfo", unavailable_zone)

    with pytest.raises(ZoneInfoNotFoundError):
        financial_date(datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc))


def test_expiry_is_start_of_next_belem_day_stored_as_utc():
    assert local_expiry_utc(date(2026, 9, 21)) == datetime(2026, 9, 22, 3, 0, tzinfo=timezone.utc)


def test_expiry_boundary_is_inclusive_and_rejects_naive_datetimes():
    expiry = local_expiry_utc(date(2026, 9, 21))
    assert not is_expired(now=datetime(2026, 9, 22, 2, 59, 59, tzinfo=timezone.utc), expires_at=expiry)
    assert is_expired(now=expiry, expires_at=expiry)
    assert is_expired(now=datetime(2026, 9, 22, 3, 0, 1, tzinfo=timezone.utc), expires_at=expiry)
    with pytest.raises(ValueError, match="timezone-aware"):
        is_expired(now=datetime(2026, 9, 22, 3, 0), expires_at=expiry)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("0"), "0.00"),
        (Decimal("1"), "1.00"),
        (Decimal("1.2"), "1.20"),
        (Decimal("1.23"), "1.23"),
    ],
)
def test_normalize_money(value, expected):
    assert normalize_money(value) == expected


def test_normalize_money_rejects_fractional_cent_negative_and_float():
    with pytest.raises(ValueError, match="cents"):
        normalize_money(Decimal("1.234"))
    with pytest.raises(ValueError, match="negative"):
        normalize_money(Decimal("-1.00"))
    with pytest.raises(TypeError, match="Decimal"):
        normalize_money(1.0)


def test_snapshot_contains_minimal_contract_and_calculates_total():
    snapshot = _snapshot()
    assert snapshot == {
        "snapshot_version": LOAN_INSTALLMENT_PIX_SNAPSHOT_VERSION,
        "reference_type": "LOAN_INSTALLMENT",
        "reference_id": "123",
        "loan_id": "45",
        "installment_id": "123",
        "installment_number": 3,
        "calculated_for_date": "2026-09-21",
        "financial_timezone": FINANCIAL_TIMEZONE,
        "principal_due": "100.00",
        "normal_price_interest_due": "20.00",
        "fixed_penalty_due": "0.00",
        "late_interest_due": "0.00",
        "total_due": "120.00",
    }


def test_snapshot_is_deterministic_and_does_not_mutate_inputs():
    components = {
        "principal_due": Decimal("100.00"),
        "normal_price_interest_due": Decimal("20.00"),
        "fixed_penalty_due": Decimal("0.00"),
        "late_interest_due": Decimal("0.00"),
    }
    before = components.copy()
    first = _snapshot(**components)
    second = _snapshot(**components)
    assert first == second
    assert components == before


def test_canonical_json_ignores_input_mapping_order_and_whitespace():
    first = {"b": "2", "a": "1"}
    second = {"a": "1", "b": "2"}
    assert canonical_json(first) == '{"a":"1","b":"2"}'
    assert canonical_json(first) == canonical_json(second)


def test_hash_is_stable_lowercase_sha256_and_changes_with_component():
    first = snapshot_hash(_snapshot())
    second = snapshot_hash(dict(reversed(list(_snapshot().items()))))
    changed = snapshot_hash(_snapshot(principal_due=Decimal("100.01")))
    assert len(first) == 64
    assert first == first.lower()
    assert first == second
    assert first != changed


def _evaluate(**overrides):
    values = {
        "attempt_status": "PENDING",
        "calculated_for_date": date(2026, 9, 21),
        "expires_at": local_expiry_utc(date(2026, 9, 21)),
        "stored_snapshot_hash": "hash",
        "current_financial_date": date(2026, 9, 21),
        "now": _now(),
        "current_snapshot_hash": "hash",
    }
    values.update(overrides)
    return evaluate_reuse(**values).decision


def test_reuse_decision_accepts_valid_pending_attempt():
    assert _evaluate() is ReuseDecision.REUSE


def test_reuse_decision_expires_at_exact_boundary_and_after():
    expiry = local_expiry_utc(date(2026, 9, 21))
    assert _evaluate(now=expiry) is ReuseDecision.EXPIRE
    assert _evaluate(now=datetime(2026, 9, 22, 3, 0, 1, tzinfo=timezone.utc)) is ReuseDecision.EXPIRE


def test_reuse_decision_supersedes_mismatched_hash_or_date_before_expiry():
    assert _evaluate(current_snapshot_hash="other") is ReuseDecision.SUPERSEDE
    assert _evaluate(calculated_for_date=date(2026, 9, 20)) is ReuseDecision.SUPERSEDE


@pytest.mark.parametrize("attempt_status", [None, "EXPIRED", "SUPERSEDED", "APPROVED"])
def test_non_pending_attempt_is_never_reused(attempt_status):
    assert _evaluate(attempt_status=attempt_status) is ReuseDecision.NO_REUSABLE_ATTEMPT


def test_pending_attempt_without_expiry_is_invalid():
    with pytest.raises(ValueError, match="expires_at"):
        _evaluate(expires_at=None)
