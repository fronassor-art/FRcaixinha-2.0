from decimal import Decimal

from app.services.loan_amortization import calculate_linear_amortization


def test_linear_amortization_uses_outstanding_balance():
    rows, total_principal, final_balance = calculate_linear_amortization(
        "100.00", "0.20", 6
    )

    assert [row["interest"] for row in rows] == [
        Decimal("20.00"),
        Decimal("16.67"),
        Decimal("13.33"),
        Decimal("10.00"),
        Decimal("6.66"),
        Decimal("3.33"),
    ]

    assert [row["amount"] for row in rows] == [
        Decimal("36.67"),
        Decimal("33.34"),
        Decimal("30.00"),
        Decimal("26.67"),
        Decimal("23.33"),
        Decimal("19.98"),
    ]

    assert total_principal == Decimal("100.00")
    assert final_balance == Decimal("0.00")
