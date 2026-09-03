from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")

def money(v: Decimal) -> Decimal:
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)

def remaining_balance(principal: Decimal, total_payable: Decimal, paid: Decimal) -> Decimal:
    return max(Decimal("0.00"), money(total_payable - paid))

def apply_installment_payment(installment_amount: Decimal, payment_amount: Decimal,
                              already_paid: Decimal = Decimal("0.00")):
    installment_amount = money(installment_amount)
    already_paid = money(already_paid)
    payment_amount = money(payment_amount)
    due = max(Decimal("0.00"), money(installment_amount - already_paid))
    applied = min(due, payment_amount)
    new_paid = money(already_paid + applied)
    return {
        "applied": applied,
        "installment_paid": new_paid >= installment_amount,
        "remaining_installment": money(installment_amount - new_paid),
        "excess": money(payment_amount - applied),
    }
