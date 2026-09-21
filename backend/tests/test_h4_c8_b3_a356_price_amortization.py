from decimal import Decimal

import pytest

from app.services.loan_amortization import (
    PRICE_CALCULATION_VERSION,
    calculate_price_amortization,
)


CENT = Decimal("0.01")


def test_price_150_six_installments_matches_approved_schedule():
    rows, total_principal, final_balance = calculate_price_amortization(
        Decimal("150.00"), Decimal("0.20"), 6
    )

    assert [
        (row["balance_before"], row["interest"], row["principal"],
         row["amount"], row["balance_after"])
        for row in rows
    ] == [
        (Decimal("150.00"), Decimal("30.00"), Decimal("15.11"), Decimal("45.11"), Decimal("134.89")),
        (Decimal("134.89"), Decimal("26.98"), Decimal("18.13"), Decimal("45.11"), Decimal("116.76")),
        (Decimal("116.76"), Decimal("23.35"), Decimal("21.76"), Decimal("45.11"), Decimal("95.00")),
        (Decimal("95.00"), Decimal("19.00"), Decimal("26.11"), Decimal("45.11"), Decimal("68.89")),
        (Decimal("68.89"), Decimal("13.78"), Decimal("31.33"), Decimal("45.11"), Decimal("37.56")),
        (Decimal("37.56"), Decimal("7.51"), Decimal("37.56"), Decimal("45.07"), Decimal("0.00")),
    ]
    assert total_principal == Decimal("150.00")
    assert sum(row["interest"] for row in rows) == Decimal("120.62")
    assert sum(row["amount"] for row in rows) == Decimal("270.62")
    assert final_balance == Decimal("0.00")


@pytest.mark.parametrize(
    ("installments", "regular", "last", "interest", "total"),
    [
        (1, "180.00", "180.00", "30.00", "180.00"),
        (2, "98.18", "98.18", "46.36", "196.36"),
        (3, "71.21", "71.21", "63.63", "213.63"),
        (4, "57.94", "57.96", "81.78", "231.78"),
        (5, "50.16", "50.14", "100.78", "250.78"),
        (6, "45.11", "45.07", "120.62", "270.62"),
    ],
)
def test_price_150_reference_totals(installments, regular, last, interest, total):
    rows, total_principal, final_balance = calculate_price_amortization(
        Decimal("150.00"), Decimal("0.20"), installments
    )
    assert rows[-1]["amount"] == Decimal(last)
    assert all(row["amount"] == Decimal(regular) for row in rows[:-1])
    assert total_principal == Decimal("150.00")
    assert sum(row["interest"] for row in rows) == Decimal(interest)
    assert sum(row["amount"] for row in rows) == Decimal(total)
    assert final_balance == Decimal("0.00")


def test_price_1000_six_installments():
    rows, total_principal, final_balance = calculate_price_amortization(
        Decimal("1000.00"), Decimal("0.20"), 6
    )
    assert [row["amount"] for row in rows[:-1]] == [Decimal("300.71")] * 5
    assert rows[-1]["amount"] == Decimal("300.67")
    assert total_principal == Decimal("1000.00")
    assert sum(row["interest"] for row in rows) == Decimal("804.22")
    assert sum(row["amount"] for row in rows) == Decimal("1804.22")
    assert final_balance == Decimal("0.00")


def test_price_invariants_and_determinism():
    result_a = calculate_price_amortization(Decimal("150.00"), Decimal("0.20"), 6)
    result_b = calculate_price_amortization(Decimal("150.00"), Decimal("0.20"), 6)
    rows, total_principal, final_balance = result_a
    assert result_a == result_b
    assert total_principal == Decimal("150.00")
    assert sum(row["amount"] for row in rows) == (
        total_principal + sum(row["interest"] for row in rows)
    )
    assert final_balance == Decimal("0.00")
    for row in rows:
        assert row["principal"] >= 0
        assert row["interest"] >= 0
        assert row["amount"] >= 0
        assert all(value == value.quantize(CENT) for value in row.values() if isinstance(value, Decimal))


def test_price_round_half_up_crosses_half_cent():
    rows, _, _ = calculate_price_amortization(
        Decimal("1.00"), Decimal("0.005"), 1
    )
    assert rows[0]["interest"] == Decimal("0.01")
    assert rows[0]["amount"] == Decimal("1.01")


def test_price_zero_rate_is_deterministic():
    rows, total_principal, final_balance = calculate_price_amortization(
        Decimal("100.00"), Decimal("0.00"), 3
    )
    assert [row["amount"] for row in rows] == [
        Decimal("33.33"), Decimal("33.33"), Decimal("33.34")
    ]
    assert total_principal == Decimal("100.00")
    assert final_balance == Decimal("0.00")


@pytest.mark.parametrize(
    ("principal", "rate", "installments"),
    [
        (Decimal("0.00"), Decimal("0.20"), 1),
        (Decimal("-1.00"), Decimal("0.20"), 1),
        (Decimal("100.00"), Decimal("-0.01"), 1),
        (Decimal("100.00"), Decimal("0.20"), 0),
        (Decimal("100.00"), Decimal("0.20"), 7),
    ],
)
def test_price_rejects_invalid_values(principal, rate, installments):
    with pytest.raises(ValueError):
        calculate_price_amortization(principal, rate, installments)


def test_price_rejects_float_financial_inputs():
    with pytest.raises(TypeError):
        calculate_price_amortization(100.0, Decimal("0.20"), 6)
    with pytest.raises(TypeError):
        calculate_price_amortization(Decimal("100.00"), 0.20, 6)


def test_price_version_is_explicit_and_linear_version_is_not_replaced():
    assert PRICE_CALCULATION_VERSION == "price_amortization_v1"
