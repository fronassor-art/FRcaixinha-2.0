from decimal import Decimal

from app.models import MemberFinancialEntry
from app.services.member_financial import calculate_member_financial_position


def entry(entry_type, direction, amount):
    return MemberFinancialEntry(
        entry_type=entry_type,
        direction=direction,
        amount=Decimal(amount),
    )


def test_contribution_creates_own_balance():
    position = calculate_member_financial_position([
        entry("CONTRIBUTION", "CREDIT", "150.00"),
    ])

    assert position["own_balance"] == Decimal("150.00")
    assert position["committed_balance"] == Decimal("0.00")
    assert position["available_balance"] == Decimal("150.00")


def test_principal_payment_returns_to_own_balance():
    position = calculate_member_financial_position([
        entry("CONTRIBUTION", "CREDIT", "150.00"),
        entry("LOAN_PRINCIPAL_COMMITMENT", "CREDIT", "100.00"),
        entry("LOAN_PRINCIPAL_PAYMENT", "CREDIT", "20.00"),
    ])

    assert position["own_balance"] == Decimal("170.00")
    assert position["committed_balance"] == Decimal("80.00")
    assert position["available_balance"] == Decimal("90.00")


def test_interest_does_not_return_to_member_balance():
    position = calculate_member_financial_position([
        entry("CONTRIBUTION", "CREDIT", "150.00"),
        entry("LOAN_PRINCIPAL_COMMITMENT", "CREDIT", "100.00"),
        entry("LOAN_PRINCIPAL_PAYMENT", "CREDIT", "20.00"),
        entry("LOAN_INTEREST_PAYMENT", "CREDIT", "20.00"),
    ])

    assert position["own_balance"] == Decimal("170.00")
    assert position["committed_balance"] == Decimal("80.00")
    assert position["available_balance"] == Decimal("90.00")


def test_full_settlement_releases_commitment():
    position = calculate_member_financial_position([
        entry("CONTRIBUTION", "CREDIT", "150.00"),
        entry("LOAN_PRINCIPAL_COMMITMENT", "CREDIT", "100.00"),
        entry("OWN_BALANCE_SETTLEMENT", "DEBIT", "100.00"),
    ])

    assert position["own_balance"] == Decimal("50.00")
    assert position["committed_balance"] == Decimal("0.00")
    assert position["available_balance"] == Decimal("50.00")


def test_available_balance_never_goes_negative():
    position = calculate_member_financial_position([
        entry("CONTRIBUTION", "CREDIT", "50.00"),
        entry("LOAN_PRINCIPAL_COMMITMENT", "CREDIT", "100.00"),
    ])

    assert position["own_balance"] == Decimal("50.00")
    assert position["committed_balance"] == Decimal("100.00")
    assert position["available_balance"] == Decimal("0.00")
