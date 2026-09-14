from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from calendar import monthrange
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models import User, Member, Contribution, Loan, LoanInstallment, LedgerEntry, Expense, Payment, PaymentSettlement
from app.services.loan_engine_v17 import installment_due
from app.services.payment_settlement import contribution_financial_status, installment_financial_status
ZERO=Decimal('0.00'); CENT=Decimal('0.01')
def money(v): return str(Decimal(v or 0).quantize(CENT,rounding=ROUND_HALF_UP))
def month_bounds(c): return c.replace(day=1),c.replace(day=monthrange(c.year,c.month)[1])
def _range(a,b): return datetime.combine(a,datetime.min.time(),tzinfo=timezone.utc),datetime.combine(b,datetime.min.time(),tzinfo=timezone.utc).replace(day=b.day)+__import__('datetime').timedelta(days=1)
def _ledger(db,types,start,end): return Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount),0)).filter(LedgerEntry.reference_type.in_(types),LedgerEntry.created_at>=start,LedgerEntry.created_at<end).scalar() or 0)
def _cp(c): return Decimal(c.paid_amount if c.paid_amount is not None else (c.amount if c.status=='PAID' else 0))
def _payment(db,t,i,legacy=None): return db.query(Payment).filter(Payment.reference_type==t,Payment.reference_id==str(i)).order_by(Payment.id.desc()).first() or (db.get(Payment,legacy) if legacy else None)
def _receipt(db,p): return bool(p and db.query(PaymentSettlement).filter(PaymentSettlement.payment_id==p.id).first())
def monthly_report(db,competence):
 a,b=month_bounds(competence);start,end=_range(a,b); contrib=_ledger(db,['CONTRIBUTION_PAYMENT'],start,end); interest=_ledger(db,['LOAN_INTEREST_PAYMENT'],start,end); penalty=_ledger(db,['LOAN_PENALTY_PAYMENT'],start,end); expenses=Decimal(db.query(func.coalesce(func.sum(Expense.amount),0)).filter(Expense.status=='POSTED',Expense.expense_date.between(a,b)).scalar() or 0); credits=Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount),0)).filter(LedgerEntry.direction=='CREDIT',LedgerEntry.created_at>=start,LedgerEntry.created_at<end).scalar() or 0); debits=Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount),0)).filter(LedgerEntry.direction=='DEBIT',LedgerEntry.created_at>=start,LedgerEntry.created_at<end).scalar() or 0)
 return {'competence':a.isoformat(),'period_end':b.isoformat(),'contributions_paid':money(contrib),'expenses':money(expenses),'interest_received':money(interest),'penalties_received':money(penalty),'operating_result':money(contrib+interest+penalty-expenses),'ledger_credits_in_period':money(credits),'ledger_debits_in_period':money(debits)}
def member_statement(db,member_id):
 m=db.get(Member,member_id)
 if not m:return None
 u=db.get(User,m.user_id); cs=db.query(Contribution).filter(Contribution.member_id==member_id).order_by(Contribution.competence.desc()).all(); loans=db.query(Loan).filter(Loan.member_id==member_id).order_by(Loan.id.desc()).all(); ids=[x.id for x in loans]; inst=db.query(LoanInstallment).filter(LoanInstallment.loan_id.in_(ids)).order_by(LoanInstallment.due_date).all() if ids else []; now=datetime.now(timezone.utc)
 contrib=sum((_cp(c) for c in cs),ZERO); loan_paid=sum((Decimal(i.paid_amount or 0)+Decimal(i.paid_penalty_amount or 0) for i in inst),ZERO); out=sum((Decimal(installment_due(i)) for i in inst),ZERO)
 return {'member':{'id':m.id,'name':u.name,'email':u.email,'cpf':u.cpf,'phone':u.phone,'status':m.status,'group_id':m.group_id},'totals':{'contributions_paid':money(contrib),'loan_payments':money(loan_paid),'loan_outstanding':money(out)},'contributions':[{'id':c.id,'competence':c.competence.isoformat(),'amount':money(c.amount),'paid_amount':money(_cp(c)),'outstanding':money(max(ZERO,Decimal(c.amount)-_cp(c))),'due_date':c.due_date.isoformat() if c.due_date else None,'status':contribution_financial_status(c,_cp(c),now),'payment_id':(_payment(db,'CONTRIBUTION',c.id,c.payment_id).id if _payment(db,'CONTRIBUTION',c.id,c.payment_id) else None),'receipt_available':_receipt(db,_payment(db,'CONTRIBUTION',c.id,c.payment_id))} for c in cs],'loans':[{'id':l.id,'principal':money(l.principal),'monthly_rate':str(l.monthly_rate),'installments':l.installments,'status':l.status} for l in loans],'installments':[{'id':i.id,'loan_id':i.loan_id,'number':i.number,'due_date':i.due_date.isoformat(),'amount':money(i.amount),'paid_amount':money(i.paid_amount),'paid_penalty_amount':money(i.paid_penalty_amount),'outstanding':money(installment_due(i)),'status':installment_financial_status(i,now),'payment_id':(_payment(db,'LOAN_INSTALLMENT',i.id).id if _payment(db,'LOAN_INSTALLMENT',i.id) else None),'receipt_available':_receipt(db,_payment(db,'LOAN_INSTALLMENT',i.id))} for i in inst]}
def loan_report(db):
 rows=[]
 for l in db.query(Loan).order_by(Loan.id.desc()).all():
  m=db.get(Member,l.member_id);u=db.get(User,m.user_id) if m else None; ins=db.query(LoanInstallment).filter(LoanInstallment.loan_id==l.id).all(); rows.append({'loan_id':l.id,'member_id':l.member_id,'member_name':u.name if u else None,'principal':money(l.principal),'status':l.status,'installments':l.installments,'interest_total':money(sum((Decimal(i.interest) for i in ins),ZERO)),'outstanding':money(sum((Decimal(installment_due(i)) for i in ins),ZERO))})
 return {'items':rows}
def delinquency_report(db):
 from app.services.financial_obligations import all_obligations
 rows=[x for x in all_obligations(db) if x['financial_status']=='OVERDUE']; return {'as_of':date.today().isoformat(),'count':len(rows),'total_outstanding':money(sum((Decimal(x['outstanding_amount']) for x in rows),ZERO)),'items':rows}
