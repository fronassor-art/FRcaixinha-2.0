from decimal import Decimal, ROUND_HALF_UP, localcontext

from app.core.loan_rules import LOAN_CALCULATION_VERSION, MAX_LOAN_INSTALLMENTS


PRICE_CALCULATION_VERSION = "price_amortization_v1"
_CENT = Decimal("0.01")


def _require_price_decimal(value, name):
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal")
    return value


def calculate_price_amortization(principal, monthly_rate, installments):
    """Calculate a deterministic Price schedule without changing the live engine."""
    principal = _require_price_decimal(principal, "principal")
    monthly_rate = _require_price_decimal(monthly_rate, "monthly_rate")

    if not isinstance(installments, int) or isinstance(installments, bool):
        raise TypeError("installments must be an int")
    if principal <= 0:
        raise ValueError("principal must be greater than zero")
    if monthly_rate < 0:
        raise ValueError("monthly_rate must not be negative")
    if not 1 <= installments <= MAX_LOAN_INSTALLMENTS:
        raise ValueError(
            f"installments must be between 1 and {MAX_LOAN_INSTALLMENTS}"
        )

    with localcontext() as context:
        context.prec = max(context.prec, 50)
        if monthly_rate == 0:
            payment = (principal / installments).quantize(
                _CENT, rounding=ROUND_HALF_UP
            )
        else:
            one_plus_rate = Decimal("1") + monthly_rate
            discount_factor = Decimal("1") / (one_plus_rate ** installments)
            theoretical_payment = (
                principal * monthly_rate / (Decimal("1") - discount_factor)
            )
            payment = theoretical_payment.quantize(
                _CENT, rounding=ROUND_HALF_UP
            )

        balance = principal.quantize(_CENT, rounding=ROUND_HALF_UP)
        total_principal = Decimal("0.00")
        rows = []

        for number in range(1, installments + 1):
            opening_balance = balance.quantize(_CENT, rounding=ROUND_HALF_UP)
            interest = (opening_balance * monthly_rate).quantize(
                _CENT, rounding=ROUND_HALF_UP
            )

            if number == installments:
                installment_principal = opening_balance
                amount = (installment_principal + interest).quantize(
                    _CENT, rounding=ROUND_HALF_UP
                )
            else:
                amount = payment
                installment_principal = (amount - interest).quantize(
                    _CENT, rounding=ROUND_HALF_UP
                )
                if installment_principal < 0:
                    raise ValueError("calculated principal cannot be negative")

            balance = (opening_balance - installment_principal).quantize(
                _CENT, rounding=ROUND_HALF_UP
            )
            rows.append(
                {
                    "number": number,
                    "principal": installment_principal,
                    "interest": interest,
                    "balance_after": balance,
                    "amount": amount,
                    "balance_before": opening_balance,
                }
            )
            total_principal += installment_principal

    return rows, total_principal.quantize(_CENT), balance


def calculate_amortization(calculation_version, principal, monthly_rate, installments):
    """Select an explicitly versioned engine; never fall back silently."""
    if calculation_version == LOAN_CALCULATION_VERSION:
        return calculate_linear_amortization(principal, monthly_rate, installments)
    if calculation_version == PRICE_CALCULATION_VERSION:
        return calculate_price_amortization(principal, monthly_rate, installments)
    if calculation_version is None:
        raise ValueError("calculation_version is required to generate a schedule")
    raise ValueError(f"Unknown loan calculation version: {calculation_version}")


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


def build_price_loan_simulation(principal, monthly_rate, installments):
    """Build a Price simulation snapshot without changing the legacy builder."""
    principal = principal.quantize(_CENT, rounding=ROUND_HALF_UP)
    rows, total_principal, final_balance = calculate_price_amortization(
        principal,
        monthly_rate,
        installments,
    )
    total_interest = sum((row["interest"] for row in rows), Decimal("0.00"))
    total_payment = sum((row["amount"] for row in rows), Decimal("0.00"))
    return {
        "calculation_version": PRICE_CALCULATION_VERSION,
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
