from decimal import Decimal


MAX_LOAN_INSTALLMENTS = 6
OFFICIAL_LOAN_MONTHLY_RATE = Decimal("0.20")
LOAN_CALCULATION_VERSION = "linear_amortization_v1"
LOAN_SIMULATION_TTL_MINUTES = 30


def validate_loan_installments(installments: int) -> None:
    if not 1 <= installments <= MAX_LOAN_INSTALLMENTS:
        raise ValueError(
            f"Empréstimos devem ter entre 1 e {MAX_LOAN_INSTALLMENTS} parcelas."
        )
