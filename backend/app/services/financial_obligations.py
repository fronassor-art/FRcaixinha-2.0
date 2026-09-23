from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from app.models import AgreementInstallment, CollectionAgreement, Contribution, ContributionChargeEvent, Loan, LoanInstallment, Payment, PaymentSettlement
from app.services.loan_engine_v17 import installment_due
from app.services.payment_settlement import contribution_financial_status, installment_financial_status
from app.services.late_charge_v1 import financial_civil_date
CENT=Decimal('0.01'); ZERO=Decimal('0.00')
def money(v): return Decimal(v or 0).quantize(CENT,rounding=ROUND_HALF_UP)
def _payment(db,t,i,legacy=None): return db.query(Payment).filter(Payment.reference_type==t,Payment.reference_id==str(i)).order_by(Payment.id.desc()).first() or (db.get(Payment,legacy) if legacy else None)
def _receipt(db,p): return bool(p and db.query(PaymentSettlement).filter(PaymentSettlement.payment_id==p.id).first())
def contribution_item(db, c, as_of=None):
 as_of = as_of or datetime.now(timezone.utc)
 paid = money(c.paid_amount if c.paid_amount is not None else (c.amount if c.status == 'PAID' else 0))
 principal_open = ZERO if c.cancelled_at is not None else max(ZERO, money(c.amount) - paid)
 status = contribution_financial_status(c, paid, as_of)
 event = db.query(ContributionChargeEvent).filter(
  ContributionChargeEvent.contribution_id == c.id,
 ).order_by(ContributionChargeEvent.accrued_through.desc(), ContributionChargeEvent.id.desc()).first()
 penalty = money(event.fixed_penalty) if event else ZERO
 interest = money(event.daily_interest) if event else ZERO
 outstanding = money(principal_open + penalty + interest)
 p = _payment(db, 'CONTRIBUTION', c.id, c.payment_id)
 return {
  'member_id': c.member_id, 'obligation_type': 'CONTRIBUTION', 'obligation_id': c.id,
  'competence': c.competence.isoformat(), 'loan_id': None, 'installment_number': None,
  'due_date': c.due_date.isoformat() if c.due_date else None,
  'amount_due': str(money(paid + outstanding)), 'amount_paid': str(paid),
  'outstanding_amount': str(outstanding), 'principal_outstanding': str(principal_open),
  'cancelled_principal': str(money(event.cancelled_principal) if event else ZERO),
  'financial_status': status,
  'days_overdue': max(0, (financial_civil_date(as_of) - c.due_date).days) if status == 'OVERDUE' and c.due_date else 0,
  'penalty_outstanding': str(penalty), 'interest_outstanding': str(interest),
  'charge_rule_version': event.rule_version if event else None,
  'charge_accrued_through': event.accrued_through.isoformat() if event else None,
  'payment_id': p.id if p else None, 'receipt_available': _receipt(db, p),
 }
def installment_item(db,i,as_of=None):
 as_of=as_of or datetime.now(timezone.utc); loan=db.get(Loan,i.loan_id); paid=money(i.paid_amount)+money(i.paid_penalty_amount); out=money(installment_due(i)); status=installment_financial_status(i,as_of); p=_payment(db,'LOAN_INSTALLMENT',i.id)
 principal_paid=max(ZERO,money(i.paid_amount)-money(i.interest))
 principal_outstanding=max(ZERO,money(i.principal)-min(money(i.principal),principal_paid))
 return {'member_id':loan.member_id,'obligation_type':'LOAN_INSTALLMENT','obligation_id':i.id,'competence':None,'loan_id':i.loan_id,'installment_number':i.number,'due_date':i.due_date.isoformat(),'amount_due':str(money(i.amount)+money(i.penalty_amount)),'amount_paid':str(paid),'outstanding_amount':str(out),'financial_status':status,'days_overdue':max(0,(as_of.date()-i.due_date).days) if status=='OVERDUE' else 0,'principal_outstanding':str(principal_outstanding),'penalty_outstanding':str(max(ZERO,money(i.penalty_amount)-money(i.paid_penalty_amount))),'interest_outstanding':str(max(ZERO,money(i.interest)-min(money(i.interest),money(i.paid_amount)))),'payment_id':p.id if p else None,'receipt_available':_receipt(db,p)}
def agreement_installment_item(db, installment, agreement, as_of=None):
 as_of=as_of or datetime.now(timezone.utc)
 principal_due=money(installment.principal)
 principal_paid=money(installment.paid_amount)
 penalty_due=money(installment.penalty_amount)
 penalty_paid=money(installment.paid_penalty_amount)
 principal_remaining=max(ZERO, principal_due-principal_paid)
 penalty_remaining=max(ZERO, penalty_due-penalty_paid)
 out=money(principal_remaining+penalty_remaining)
 paid=money(principal_paid+penalty_paid)
 if out == ZERO: status='PAID'
 elif installment.due_date < as_of.date(): status='OVERDUE'
 elif paid > ZERO: status='PARTIAL'
 else: status='PENDING'
 p=_payment(db,'AGREEMENT_INSTALLMENT',installment.id)
 return {'member_id':agreement.member_id,'obligation_type':'AGREEMENT_INSTALLMENT','obligation_id':installment.id,'competence':None,'loan_id':agreement.loan_id,'installment_number':installment.number,'due_date':installment.due_date.isoformat(),'amount_due':str(money(principal_due+penalty_due)),'amount_paid':str(paid),'outstanding_amount':str(out),'financial_status':status,'days_overdue':max(0,(as_of.date()-installment.due_date).days) if status=='OVERDUE' else 0,'principal_outstanding':str(principal_remaining),'penalty_outstanding':str(penalty_remaining),'interest_outstanding':'0.00','payment_id':p.id if p else None,'receipt_available':_receipt(db,p)}
def _agreement_items(db, member_id=None):
 q=db.query(AgreementInstallment,CollectionAgreement).join(CollectionAgreement,CollectionAgreement.id==AgreementInstallment.agreement_id).filter(
  (CollectionAgreement.status=='APPROVED') | ((CollectionAgreement.status=='SETTLED') & (AgreementInstallment.status=='PAID'))
 )
 if member_id is not None: q=q.filter(CollectionAgreement.member_id==member_id)
 return q.all()
def member_obligations(db,member_id):
 items=[contribution_item(db,c) for c in db.query(Contribution).filter(Contribution.member_id==member_id)]; ids=[x.id for x in db.query(Loan).filter(Loan.member_id==member_id)]
 loan_items=[installment_item(db,i) for i in db.query(LoanInstallment).filter(LoanInstallment.loan_id.in_(ids))] if ids else []
 agreement_items=[agreement_installment_item(db,i,a) for i,a in _agreement_items(db,member_id)]
 return items+loan_items+agreement_items
def all_obligations(db):
 return [contribution_item(db,c) for c in db.query(Contribution).all()]+[installment_item(db,i) for i in db.query(LoanInstallment).all()]+[agreement_installment_item(db,i,a) for i,a in _agreement_items(db)]
