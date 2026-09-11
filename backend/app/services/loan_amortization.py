from decimal import Decimal, ROUND_HALF_UP


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
                "amount": amount,
                "balance_before": balance,
            }
        )

        total_principal += installment_principal
        balance -= installment_principal

    return rows, total_principal, balance
