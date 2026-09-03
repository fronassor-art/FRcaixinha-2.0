"""
FRcaixinha 2.0 - v0.6 Loan Domain

Approval is administrator-only and idempotent.
Interest is configurable and must be legally validated before production.
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")

def money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)

def simple_interest(principal: Decimal, monthly_rate_percent: Decimal, months: int) -> Decimal:
    return money(principal * monthly_rate_percent / Decimal("100") * Decimal(months))

def build_installments(principal: Decimal, monthly_rate_percent: Decimal,
                       months: int, first_due_date: date):
    if months <= 0:
        raise ValueError("months must be positive")
    principal = money(principal)
    interest = simple_interest(principal, monthly_rate_percent, months)
    total = money(principal + interest)
    base = money(total / Decimal(months))
    items = []
    allocated = Decimal("0.00")
    for n in range(1, months + 1):
        amount = base if n < months else money(total - allocated)
        allocated = money(allocated + amount)
        items.append({
            "number": n,
            "due_date": first_due_date,  # adapter can apply monthly calendar rules
            "amount": amount,
        })
    return {"principal": principal, "interest": interest, "total": total, "installments": items}
