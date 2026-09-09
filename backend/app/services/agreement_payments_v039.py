from datetime import datetime, timezone
from decimal import Decimal
from app.models import AgreementInstallment, CollectionAgreement, Payment, LedgerEntry
from app.services.loan_engine_v17 import money

def apply_confirmed_agreement_payment(db, payment:Payment, installment:AgreementInstallment):
    if payment.ledger_posted_at is not None: return False
    penalty_open=max(Decimal('0'),money(installment.penalty_amount)-money(installment.paid_penalty_amount))
    principal_open=max(Decimal('0'),money(installment.principal)-money(installment.paid_amount))
    due=money(penalty_open+principal_open); applied=min(money(payment.amount),due)
    if applied<=0:
        payment.ledger_posted_at=datetime.now(timezone.utc); return False
    pen=min(penalty_open,applied); principal=min(principal_open,money(applied-pen))
    installment.paid_penalty_amount=money(installment.paid_penalty_amount+pen); installment.paid_amount=money(installment.paid_amount+principal)
    installment.status='PAID' if money((installment.penalty_amount-installment.paid_penalty_amount)+(installment.principal-installment.paid_amount))<=0 else 'PARTIAL'
    if installment.status=='PAID': installment.paid_at=datetime.now(timezone.utc)
    ref=str(payment.id)
    if not db.query(LedgerEntry).filter(LedgerEntry.reference_type=='AGREEMENT_INSTALLMENT_PAYMENT',LedgerEntry.reference_id==ref).first():
        db.add(LedgerEntry(account='CAIXINHA',direction='CREDIT',amount=applied,reference_type='AGREEMENT_INSTALLMENT_PAYMENT',reference_id=ref))
    payment.ledger_posted_at=datetime.now(timezone.utc)
    ag=db.get(CollectionAgreement,installment.agreement_id)
    if ag:
        items=db.query(AgreementInstallment).filter(AgreementInstallment.agreement_id==ag.id).all()
        if items and all(i.status=='PAID' for i in items): ag.status='SETTLED'
    return True
