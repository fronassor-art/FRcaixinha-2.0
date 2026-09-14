from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from app.models import Contribution, Loan, LoanInstallment, Payment, PaymentSettlement
from app.services.loan_engine_v17 import installment_due
from app.services.payment_settlement import contribution_financial_status, installment_financial_status
CENT=Decimal('0.01'); ZERO=Decimal('0.00')
def money(v): return Decimal(v or 0).quantize(CENT,rounding=ROUND_HALF_UP)
def _payment(db,t,i,legacy=None): return db.query(Payment).filter(Payment.reference_type==t,Payment.reference_id==str(i)).order_by(Payment.id.desc()).first() or (db.get(Payment,legacy) if legacy else None)
def _receipt(db,p): return bool(p and db.query(PaymentSettlement).filter(PaymentSettlement.payment_id==p.id).first())
def contribution_item(db,c,as_of=None):
 as_of=as_of or datetime.now(timezone.utc); paid=money(c.paid_amount if c.paid_amount is not None else (c.amount if c.status=='PAID' else 0)); out=max(ZERO,money(c.amount)-paid); status=contribution_financial_status(c,paid,as_of); p=_payment(db,'CONTRIBUTION',c.id,c.payment_id)
 return {'member_id':c.member_id,'obligation_type':'CONTRIBUTION','obligation_id':c.id,'competence':c.competence.isoformat(),'loan_id':None,'installment_number':None,'due_date':c.due_date.isoformat() if c.due_date else None,'amount_due':str(money(c.amount)),'amount_paid':str(paid),'outstanding_amount':str(out),'financial_status':status,'days_overdue':max(0,(as_of.date()-c.due_date).days) if status=='OVERDUE' and c.due_date else 0,'penalty_outstanding':'0.00','interest_outstanding':'0.00','payment_id':p.id if p else None,'receipt_available':_receipt(db,p)}
def installment_item(db,i,as_of=None):
 as_of=as_of or datetime.now(timezone.utc); loan=db.get(Loan,i.loan_id); paid=money(i.paid_amount)+money(i.paid_penalty_amount); out=money(installment_due(i)); status=installment_financial_status(i,as_of); p=_payment(db,'LOAN_INSTALLMENT',i.id)
 principal_paid=max(ZERO,money(i.paid_amount)-money(i.interest))
 principal_outstanding=max(ZERO,money(i.principal)-min(money(i.principal),principal_paid))
 return {'member_id':loan.member_id,'obligation_type':'LOAN_INSTALLMENT','obligation_id':i.id,'competence':None,'loan_id':i.loan_id,'installment_number':i.number,'due_date':i.due_date.isoformat(),'amount_due':str(money(i.amount)+money(i.penalty_amount)),'amount_paid':str(paid),'outstanding_amount':str(out),'financial_status':status,'days_overdue':max(0,(as_of.date()-i.due_date).days) if status=='OVERDUE' else 0,'principal_outstanding':str(principal_outstanding),'penalty_outstanding':str(max(ZERO,money(i.penalty_amount)-money(i.paid_penalty_amount))),'interest_outstanding':str(max(ZERO,money(i.interest)-min(money(i.interest),money(i.paid_amount)))),'payment_id':p.id if p else None,'receipt_available':_receipt(db,p)}
def member_obligations(db,member_id):
 items=[contribution_item(db,c) for c in db.query(Contribution).filter(Contribution.member_id==member_id)]; ids=[x.id for x in db.query(Loan).filter(Loan.member_id==member_id)]
 return items+([installment_item(db,i) for i in db.query(LoanInstallment).filter(LoanInstallment.loan_id.in_(ids))] if ids else [])
def all_obligations(db): return [contribution_item(db,c) for c in db.query(Contribution).all()]+[installment_item(db,i) for i in db.query(LoanInstallment).all()]
