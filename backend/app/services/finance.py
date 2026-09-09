from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")

def simple_interest(principal: Decimal, monthly_rate: Decimal, months: int) -> Decimal:
    return (principal * monthly_rate * Decimal(months)).quantize(CENT, rounding=ROUND_HALF_UP)

def service_fee(interest: Decimal, rate: Decimal = Decimal("0.10")) -> Decimal:
    return (interest * rate).quantize(CENT, rounding=ROUND_HALF_UP)
