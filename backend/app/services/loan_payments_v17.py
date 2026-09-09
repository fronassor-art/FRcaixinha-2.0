from datetime import datetime, timezone
from decimal import Decimal
from app.models import LoanInstallment, Payment, LedgerEntry, Loan
from app.services.loan_engine_v17 import apply_payment, ensure_loan_completion


def apply_confirmed_payment(db, payment: Payment, installment: LoanInstallment):
    if payment.ledger_posted_at is not None:
        return False
    amount = Decimal(payment.amount)
    result = apply_payment(installment, amount)
    if result['applied'] <= 0:
        payment.ledger_posted_at = datetime.now(timezone.utc)
        return False
    ref = str(payment.id)
    exists = db.query(LedgerEntry).filter(
        LedgerEntry.reference_type == 'LOAN_INSTALLMENT_PAYMENT',
        LedgerEntry.reference_id == ref,
    ).first()
    if not exists:
        db.add(LedgerEntry(account='CAIXINHA', direction='CREDIT', amount=result['applied'],
                            reference_type='LOAN_INSTALLMENT_PAYMENT', reference_id=ref))
    payment.ledger_posted_at = datetime.now(timezone.utc)
    loan = db.get(Loan, installment.loan_id)
    if loan:
        ensure_loan_completion(db, loan)
    return True
