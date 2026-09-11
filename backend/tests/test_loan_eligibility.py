from decimal import Decimal

from app.services.loan_eligibility import evaluate_loan_eligibility


def test_new_loan_is_allowed_without_existing_debt():
    result = evaluate_loan_eligibility(
        own_balance=Decimal("150.00"),
        committed_balance=Decimal("0.00"),
        outstanding_principal=Decimal("0.00"),
        normal_credit_limit=Decimal("150.00"),
        requested_amount=Decimal("100.00"),
        liquidity_available=Decimal("1000.00"),
    )

    assert result.eligible is True
    assert result.decision == "NOVO_EMPRESTIMO"
    assert result.available_balance == Decimal("150.00")


def test_existing_debt_with_available_own_balance_requires_renegotiation():
    result = evaluate_loan_eligibility(
        own_balance=Decimal("150.00"),
        committed_balance=Decimal("100.00"),
        outstanding_principal=Decimal("80.00"),
        normal_credit_limit=Decimal("150.00"),
        requested_amount=Decimal("50.00"),
        liquidity_available=Decimal("1000.00"),
    )

    assert result.eligible is False
    assert result.decision == "RENEGOCIACAO"
    assert result.available_balance == Decimal("50.00")


def test_full_settlement_with_own_balance_is_detected():
    result = evaluate_loan_eligibility(
        own_balance=Decimal("200.00"),
        committed_balance=Decimal("100.00"),
        outstanding_principal=Decimal("80.00"),
        normal_credit_limit=Decimal("200.00"),
        requested_amount=Decimal("50.00"),
        liquidity_available=Decimal("1000.00"),
    )

    assert result.eligible is False
    assert result.decision == "QUITACAO_COM_SALDO_PROPRIO"
    assert result.available_balance == Decimal("100.00")


def test_existing_debt_without_available_balance_is_denied():
    result = evaluate_loan_eligibility(
        own_balance=Decimal("100.00"),
        committed_balance=Decimal("100.00"),
        outstanding_principal=Decimal("80.00"),
        normal_credit_limit=Decimal("100.00"),
        requested_amount=Decimal("50.00"),
        liquidity_available=Decimal("1000.00"),
    )

    assert result.eligible is False
    assert result.decision == "NEGADO"
    assert result.available_balance == Decimal("0.00")


def test_amount_above_normal_limit_requires_special_credit():
    result = evaluate_loan_eligibility(
        own_balance=Decimal("500.00"),
        committed_balance=Decimal("0.00"),
        outstanding_principal=Decimal("0.00"),
        normal_credit_limit=Decimal("150.00"),
        requested_amount=Decimal("300.00"),
        liquidity_available=Decimal("1000.00"),
    )

    assert result.eligible is False
    assert result.decision == "CREDITO_ESPECIAL"


def test_insufficient_fr_caixinha_liquidity_is_denied():
    result = evaluate_loan_eligibility(
        own_balance=Decimal("500.00"),
        committed_balance=Decimal("0.00"),
        outstanding_principal=Decimal("0.00"),
        normal_credit_limit=Decimal("500.00"),
        requested_amount=Decimal("300.00"),
        liquidity_available=Decimal("250.00"),
    )

    assert result.eligible is False
    assert result.decision == "NEGADO"
    assert "Liquidez insuficiente" in result.reason
