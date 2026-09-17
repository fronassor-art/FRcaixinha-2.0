from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from calendar import monthrange
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models import User, Member, Contribution, Loan, LoanInstallment, LedgerEntry, Expense, Payment, PaymentSettlement, PaymentReversal, PaymentReversalComponent, MemberFinancialAccount, MemberFinancialEntry, CollectionAgreement, AgreementInstallment
from app.services.loan_engine_v17 import installment_due
from app.services.payment_settlement import contribution_financial_status, installment_financial_status
from app.services.payment_reversal_evidence import validate_reversal_effect
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
def _legacy_member_statement(db,member_id):
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

def _int_or_none(value):
 try:return int(value)
 except (TypeError,ValueError):return None

def _effective_at(payment, settlement, timestamps):
 if settlement is not None:return settlement.confirmed_at
 if payment is not None and payment.confirmed_at is not None:return payment.confirmed_at
 return max(timestamps) if timestamps else None

def _member_direction(movement_type):
 return 'CREDIT' if movement_type=='LOAN_DISBURSEMENT' else 'DEBIT'

def _reversal_direction(original_movement_type):
 return 'DEBIT' if _member_direction(original_movement_type)=='CREDIT' else 'CREDIT'

def _movement(movement_id,movement_type,direction,occurred_at,description,total,competence=None,loan_id=None,installment_number=None,principal=None,interest=None,penalty=None,payment_id=None,receipt_available=False):
 return {'id':movement_id,'type':movement_type,'direction':direction,'occurred_at':occurred_at.astimezone(timezone.utc).isoformat() if occurred_at is not None else None,'description':description,'total':money(total),'competence':competence.isoformat() if competence is not None else None,'loan_id':loan_id,'installment_number':installment_number,'principal':money(principal) if principal is not None else None,'interest':money(interest) if interest is not None else None,'penalty':money(penalty) if penalty is not None else None,'payment_id':payment_id,'receipt_available':receipt_available}

def _valid_payment_reversal_index(db, member_id):
 """Index only complete A1/A2/A3 reversals for statement projection.

 The statement is allowed to show immutable ledger evidence, but only a
 reversal accepted by the shared H1 validator has economic effect here.
 """
 reversals=db.query(PaymentReversal).join(
  PaymentSettlement,PaymentSettlement.id==PaymentReversal.settlement_id
 ).filter(PaymentSettlement.member_id==member_id).order_by(PaymentReversal.id).all()
 valid_by_payment={}; valid_by_compensating={}
 for reversal in reversals:
  ok,_=validate_reversal_effect(db,reversal)
  if not ok:continue
  valid_by_payment[reversal.payment_id]=reversal
  components=db.query(PaymentReversalComponent).filter(
   PaymentReversalComponent.payment_reversal_id==reversal.id
  ).all()
  for component in components:
   valid_by_compensating[component.compensating_ledger_entry_id]=reversal
 return valid_by_payment,valid_by_compensating

def _statement_movements(db, member_id, contributions, loans, installments):
 """Project only posted movements; settlements are metadata and never monetary input."""
 contribution_by_id={row.id:row for row in contributions}
 legacy_contribution_by_payment={row.payment_id:row for row in contributions if row.payment_id is not None}
 loan_by_id={row.id:row for row in loans}; installment_by_id={row.id:row for row in installments}
 settlements={row.payment_id:row for row in db.query(PaymentSettlement).filter(PaymentSettlement.member_id==member_id).all()}
 valid_reversals,valid_compensations=_valid_payment_reversal_index(db,member_id)
 movements=[]
 # One posted contribution ledger reference equals one confirmed payment.
 contribution_rows={}
 for entry in db.query(LedgerEntry).filter(LedgerEntry.reference_type=='CONTRIBUTION_PAYMENT'):
  payment_id=_int_or_none(entry.reference_id)
  if payment_id is None:continue
  payment=db.get(Payment,payment_id)
  contribution_id=_int_or_none(payment.reference_id) if payment is not None and (payment.reference_type or '').upper()=='CONTRIBUTION' else None
  contribution=contribution_by_id.get(contribution_id) or legacy_contribution_by_payment.get(payment_id)
  if contribution is None:continue
  contribution_rows.setdefault(payment_id,{'payment':payment,'contribution':contribution,'entries':[]})['entries'].append(entry)
 for payment_id,row in contribution_rows.items():
  entries=row['entries']; settlement=settlements.get(payment_id); total=sum((Decimal(entry.amount) for entry in entries),ZERO)
  movements.append(_movement(f'payment:{payment_id}','CONTRIBUTION_PAYMENT',_member_direction('CONTRIBUTION_PAYMENT'),_effective_at(row['payment'],settlement,[entry.created_at for entry in entries]),'Pagamento de contribuição',total,competence=row['contribution'].competence,payment_id=payment_id,receipt_available=settlement is not None))
 # Group current interest/penalty ledger entries and principal account entries by payment id.
 loan_rows={}
 def loan_row(payment_id):
  payment=db.get(Payment,payment_id)
  if payment is None or (payment.reference_type or '').upper()!='LOAN_INSTALLMENT':return None
  installment=installment_by_id.get(_int_or_none(payment.reference_id))
  if installment is None:return None
  return loan_rows.setdefault(payment_id,{'payment':payment,'installment':installment,'principal':ZERO,'interest':ZERO,'penalty':ZERO,'legacy_total':ZERO,'timestamps':[]})
 for entry in db.query(LedgerEntry).filter(LedgerEntry.reference_type.in_(['LOAN_INTEREST_PAYMENT','LOAN_PENALTY_PAYMENT','LOAN_INSTALLMENT_PAYMENT'])):
  payment_id=_int_or_none(entry.reference_id)
  if payment_id is None:continue
  row=loan_row(payment_id)
  if row is None:continue
  if entry.reference_type=='LOAN_INTEREST_PAYMENT':row['interest']+=Decimal(entry.amount)
  elif entry.reference_type=='LOAN_PENALTY_PAYMENT':row['penalty']+=Decimal(entry.amount)
  else:row['legacy_total']+=Decimal(entry.amount)
  row['timestamps'].append(entry.created_at)
 for entry in db.query(MemberFinancialEntry).join(MemberFinancialAccount,MemberFinancialEntry.account_id==MemberFinancialAccount.id).filter(MemberFinancialAccount.member_id==member_id,MemberFinancialEntry.reference_type=='LOAN_PRINCIPAL_PAYMENT'):
  payment_id=_int_or_none(entry.reference_id)
  if payment_id is None:continue
  row=loan_row(payment_id)
  if row is None:continue
  row['principal']+=Decimal(entry.amount);row['timestamps'].append(entry.created_at)
 for payment_id,row in loan_rows.items():
  components=row['principal']+row['interest']+row['penalty']; total=components if components>ZERO else row['legacy_total']
  if total<=ZERO:continue
  payment=row['payment']; installment=row['installment']; loan=loan_by_id[installment.loan_id]; settlement=settlements.get(payment_id)
  movements.append(_movement(f'payment:{payment_id}','LOAN_INSTALLMENT_PAYMENT',_member_direction('LOAN_INSTALLMENT_PAYMENT'),_effective_at(payment,settlement,row['timestamps']),f'Pagamento da parcela {installment.number} do empréstimo #{loan.id}',total,loan_id=loan.id,installment_number=installment.number,principal=row['principal'] if components>ZERO else None,interest=row['interest'] if components>ZERO else None,penalty=row['penalty'] if components>ZERO else None,payment_id=payment_id,receipt_available=settlement is not None))
 # A loan appears as a debit only after its official disbursement ledger entry.
 for entry in db.query(LedgerEntry).filter(LedgerEntry.reference_type=='LOAN_DISBURSEMENT'):
  loan=loan_by_id.get(_int_or_none(entry.reference_id))
  if loan is not None:movements.append(_movement(f'ledger:{entry.id}','LOAN_DISBURSEMENT',_member_direction('LOAN_DISBURSEMENT'),entry.created_at,f'Liberação do empréstimo #{loan.id}',entry.amount,loan_id=loan.id))
 # Agreement installments are a legacy flow but are safely linked by payment and agreement.
 agreement_rows={}
 for entry in db.query(LedgerEntry).filter(LedgerEntry.reference_type=='AGREEMENT_INSTALLMENT_PAYMENT'):
  payment_id=_int_or_none(entry.reference_id); payment=db.get(Payment,payment_id) if payment_id is not None else None
  if payment is None or (payment.reference_type or '').upper()!='AGREEMENT_INSTALLMENT':continue
  installment=db.get(AgreementInstallment,_int_or_none(payment.reference_id))
  if installment is None:continue
  agreement=db.get(CollectionAgreement,installment.agreement_id)
  if agreement is None or agreement.member_id!=member_id:continue
  agreement_rows.setdefault(payment_id,{'payment':payment,'installment':installment,'agreement':agreement,'entries':[]})['entries'].append(entry)
 for payment_id,row in agreement_rows.items():
  entries=row['entries']; installment=row['installment']; agreement=row['agreement']
  movements.append(_movement(f'payment:{payment_id}','AGREEMENT_INSTALLMENT_PAYMENT',_member_direction('AGREEMENT_INSTALLMENT_PAYMENT'),_effective_at(row['payment'],None,[entry.created_at for entry in entries]),f'Pagamento da parcela {installment.number} do acordo #{agreement.id}',sum((Decimal(entry.amount) for entry in entries),ZERO),loan_id=agreement.loan_id,installment_number=installment.number,payment_id=payment_id,receipt_available=settlements.get(payment_id) is not None))
 # A reversal is another immutable ledger movement. Payment-linked entries
 # receive economic effect only when the shared H1 evidence validator accepts
 # the complete PaymentReversal chain.
 for entry in db.query(LedgerEntry).filter(LedgerEntry.reference_type=='REVERSAL'):
  original=db.get(LedgerEntry,entry.reversal_of_id or _int_or_none(entry.reference_id))
  if original is None:continue
  if original.reference_type=='LOAN_DISBURSEMENT' and loan_by_id.get(_int_or_none(original.reference_id)) is not None:
   loan=loan_by_id[_int_or_none(original.reference_id)]
   movements.append(_movement(f'ledger:{entry.id}','REVERSAL',_reversal_direction('LOAN_DISBURSEMENT'),entry.created_at,f'Reversão da liberação do empréstimo #{loan.id}',entry.amount,loan_id=loan.id))
   continue
  reversal=valid_compensations.get(entry.id)
  if reversal is None:continue
  payment_id=_int_or_none(original.reference_id)
  payment=db.get(Payment,payment_id) if payment_id is not None else None
  if payment is None or reversal.payment_id!=payment.id:continue
  occurred_at=reversal.reversed_at or entry.created_at
  if original.reference_type=='CONTRIBUTION_PAYMENT':
   contribution=contribution_by_id.get(_int_or_none(payment.reference_id)) or legacy_contribution_by_payment.get(payment.id)
   if contribution is not None:
    movements.append(_movement(f'ledger:{entry.id}','REVERSAL','CREDIT',occurred_at,'Reversão de pagamento de contribuição',entry.amount,competence=reversal.reversal_competence,payment_id=payment.id,receipt_available=True))
  elif original.reference_type in {'LOAN_INTEREST_PAYMENT','LOAN_PENALTY_PAYMENT'}:
   installment=installment_by_id.get(_int_or_none(payment.reference_id))
   if installment is not None:
    movements.append(_movement(f'ledger:{entry.id}','REVERSAL','CREDIT',occurred_at,f'Reversão de pagamento da parcela {installment.number} do empréstimo #{installment.loan_id}',entry.amount,loan_id=installment.loan_id,installment_number=installment.number,interest=entry.amount if original.reference_type=='LOAN_INTEREST_PAYMENT' else None,penalty=entry.amount if original.reference_type=='LOAN_PENALTY_PAYMENT' else None,payment_id=payment.id,receipt_available=True))
  elif original.reference_type=='AGREEMENT_INSTALLMENT_PAYMENT':
   installment=db.get(AgreementInstallment,_int_or_none(payment.reference_id)); agreement=db.get(CollectionAgreement,installment.agreement_id) if installment is not None else None
   if agreement is not None and agreement.member_id==member_id:
    movements.append(_movement(f'ledger:{entry.id}','REVERSAL','CREDIT',occurred_at,f'Reversão de pagamento da parcela {installment.number} do acordo #{agreement.id}',entry.amount,loan_id=agreement.loan_id,installment_number=installment.number,payment_id=payment.id,receipt_available=True))
 # Loan principal is a MemberFinancialEntry, not a LedgerEntry. Project its
 # compensating event separately, but only for the already validated A2.
 for reversal in valid_reversals.values():
  settlement=settlements.get(reversal.payment_id)
  if settlement is None or settlement.obligation_type!='LOAN_INSTALLMENT' or Decimal(reversal.principal_applied or 0)<=ZERO:continue
  rows=db.query(MemberFinancialEntry).join(
   MemberFinancialAccount,MemberFinancialEntry.account_id==MemberFinancialAccount.id
  ).filter(
   MemberFinancialAccount.member_id==member_id,
   MemberFinancialEntry.payment_reversal_id==reversal.id,
   MemberFinancialEntry.entry_type=='LOAN_PRINCIPAL_REVERSAL',
   MemberFinancialEntry.direction=='DEBIT',
  ).all()
  for row in rows:
   installment=installment_by_id.get(_int_or_none(db.get(Payment,reversal.payment_id).reference_id))
   if installment is None:continue
   movements.append(_movement(f'mfe:{row.id}','REVERSAL','CREDIT',reversal.reversed_at,'Reversão de principal da parcela %s do empréstimo #%s'%(installment.number,installment.loan_id),row.amount,loan_id=installment.loan_id,installment_number=installment.number,principal=row.amount,payment_id=reversal.payment_id,receipt_available=True))
 return sorted(movements,key=lambda item:(item['occurred_at'] or '',item['id']),reverse=True)

def member_statement(db, member_id):
 with db.no_autoflush:
  result=_legacy_member_statement(db,member_id)
  if result is None:return None
  contributions=db.query(Contribution).filter(Contribution.member_id==member_id).all()
  loans=db.query(Loan).filter(Loan.member_id==member_id).all(); loan_ids=[loan.id for loan in loans]
  installments=db.query(LoanInstallment).filter(LoanInstallment.loan_id.in_(loan_ids)).all() if loan_ids else []
  result['movements']=_statement_movements(db,member_id,contributions,loans,installments)
  return result
