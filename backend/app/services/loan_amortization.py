from decimal import Decimal, ROUND_HALF_UP

from app.core.loan_rules import LOAN_CALCULATION_VERSION


def calculate_linear_amortization(principal, monthly_rate, installments):
    principal = Decimal(principal)
    monthly_rate = Decimal(monthly_rate)

    principal_each = (
        principal / installments
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    total_principal = Decimal("0")
    balance = principal
    rows = []

    for number in range(1, installments + 1):
        installment_principal = (
            principal_each
            if number < installments
            else principal - total_principal
        )

        interest = (
            balance * monthly_rate
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        amount = installment_principal + interest

        rows.append(
            {
                "number": number,
                "principal": installment_principal,
                "interest": interest,
                "balance_after": balance - installment_principal,
                "amount": amount,
                "balance_before": balance,
            }
        )

        total_principal += installment_principal
        balance -= installment_principal

    return rows, total_principal, balance


def build_loan_simulation(principal, monthly_rate, installments):
    """Build the auditable public schedule from the canonical amortization routine."""
    principal = Decimal(principal).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    monthly_rate = Decimal(monthly_rate)
    rows, total_principal, final_balance = calculate_linear_amortization(
        principal,
        monthly_rate,
        installments,
    )
    total_interest = sum((row["interest"] for row in rows), Decimal("0.00"))
    total_payment = sum((row["amount"] for row in rows), Decimal("0.00"))
    return {
        "calculation_version": LOAN_CALCULATION_VERSION,
        "principal": principal,
        "monthly_rate": monthly_rate,
        "installments": installments,
        "installments_schedule": rows,
        "totals": {
            "principal": total_principal,
            "interest": total_interest,
            "payment": total_payment,
            "final_balance": final_balance,
        },
    }
