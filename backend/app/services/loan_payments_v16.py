from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
from app.models import LoanInstallment, Payment, LedgerEntry

CENT = Decimal('0.01')

def money(v):
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)

def remaining(installment):
    return max(Decimal('0.00'), money(installment.amount - (installment.paid_amount or 0)))

def apply_confirmed_payment(db, payment: Payment, installment: LoanInstallment):
    """Baixa uma parcela uma única vez e registra a entrada no Ledger."""
    if payment.ledger_posted_at is not None:
        return False
    amount = money(payment.amount)
    before = remaining(installment)
    applied = min(amount, before)
    installment.paid_amount = money((installment.paid_amount or 0) + applied)
    installment.status = 'PAID' if installment.paid_amount >= installment.amount else 'PARTIAL'
    ref = str(payment.id)
    exists = db.query(LedgerEntry).filter(
        LedgerEntry.reference_type == 'LOAN_INSTALLMENT_PAYMENT',
        LedgerEntry.reference_id == ref,
    ).first()
    if not exists and applied > 0:
        db.add(LedgerEntry(account='CAIXINHA', direction='CREDIT', amount=applied,
                            reference_type='LOAN_INSTALLMENT_PAYMENT', reference_id=ref))
    payment.ledger_posted_at = datetime.now(timezone.utc)
    return True
