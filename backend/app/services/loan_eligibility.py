from dataclasses import dataclass
from decimal import Decimal


ZERO = Decimal("0.00")


@dataclass(frozen=True)
class LoanEligibilityResult:
    eligible: bool
    decision: str
    own_balance: Decimal
    committed_balance: Decimal
    available_balance: Decimal
    outstanding_principal: Decimal
    normal_credit_limit: Decimal
    requested_amount: Decimal
    liquidity_available: Decimal
    reason: str


def evaluate_loan_eligibility(
    *,
    own_balance: Decimal,
    committed_balance: Decimal,
    outstanding_principal: Decimal,
    normal_credit_limit: Decimal,
    requested_amount: Decimal,
    liquidity_available: Decimal,
) -> LoanEligibilityResult:
    own_balance = Decimal(own_balance)
    committed_balance = Decimal(committed_balance)
    outstanding_principal = Decimal(outstanding_principal)
    normal_credit_limit = Decimal(normal_credit_limit)
    requested_amount = Decimal(requested_amount)
    liquidity_available = Decimal(liquidity_available)

    available_balance = max(
        ZERO,
        own_balance - committed_balance,
    )

    if requested_amount <= ZERO:
        return LoanEligibilityResult(
            False,
            "NEGADO",
            own_balance,
            committed_balance,
            available_balance,
            outstanding_principal,
            normal_credit_limit,
            requested_amount,
            liquidity_available,
            "O valor solicitado deve ser maior que zero.",
        )

    if liquidity_available < requested_amount:
        return LoanEligibilityResult(
            False,
            "NEGADO",
            own_balance,
            committed_balance,
            available_balance,
            outstanding_principal,
            normal_credit_limit,
            requested_amount,
            liquidity_available,
            "Liquidez insuficiente da FRcaixinha.",
        )

    if outstanding_principal > ZERO:
        if available_balance >= outstanding_principal:
            return LoanEligibilityResult(
                False,
                "QUITACAO_COM_SALDO_PROPRIO",
                own_balance,
                committed_balance,
                available_balance,
                outstanding_principal,
                normal_credit_limit,
                requested_amount,
                liquidity_available,
                "Existe dívida aberta que pode ser quitada com saldo próprio.",
            )

        if available_balance > ZERO:
            return LoanEligibilityResult(
                False,
                "RENEGOCIACAO",
                own_balance,
                committed_balance,
                available_balance,
                outstanding_principal,
                normal_credit_limit,
                requested_amount,
                liquidity_available,
                "Existe dívida aberta; operação deve passar por renegociação.",
            )

        return LoanEligibilityResult(
            False,
            "NEGADO",
            own_balance,
            committed_balance,
            available_balance,
            outstanding_principal,
            normal_credit_limit,
            requested_amount,
            liquidity_available,
            "Existe dívida principal aberta sem saldo próprio disponível.",
        )

    if requested_amount > normal_credit_limit:
        return LoanEligibilityResult(
            False,
            "CREDITO_ESPECIAL",
            own_balance,
            committed_balance,
            available_balance,
            outstanding_principal,
            normal_credit_limit,
            requested_amount,
            liquidity_available,
            "Valor solicitado excede o limite normal e exige crédito especial.",
        )

    return LoanEligibilityResult(
        True,
        "NOVO_EMPRESTIMO",
        own_balance,
        committed_balance,
        available_balance,
        outstanding_principal,
        normal_credit_limit,
        requested_amount,
        liquidity_available,
        "Participante elegível para novo empréstimo.",
    )
