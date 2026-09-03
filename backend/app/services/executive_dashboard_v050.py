import hashlib, json
from datetime import date
from decimal import Decimal
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models import (ExecutiveDashboardSnapshot, Member, Contribution, Loan, LoanInstallment,
    Expense, LedgerEntry, FinancialReconciliation, FinancialRiskAssessment, PaymentPromise)
from app.services.collections_v038 import collections_summary
from app.services.collection_recovery_v049 import collection_recovery_summary
from app.services.ledger import verify_ledger_chain

ZERO=Decimal("0.00")
def money(v): return f"{Decimal(v or 0).quantize(Decimal('0.01')):.2f}"

def build_executive_dashboard(db: Session, today: date|None=None):
    today=today or date.today()
    credits=Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount),0)).filter(LedgerEntry.direction=='CREDIT').scalar() or 0)
    debits=Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount),0)).filter(LedgerEntry.direction=='DEBIT').scalar() or 0)
    cash=credits-debits
    paid_contrib=Decimal(db.query(func.coalesce(func.sum(Contribution.amount),0)).filter(Contribution.status=='PAID').scalar() or 0)
    posted_exp=Decimal(db.query(func.coalesce(func.sum(Expense.amount),0)).filter(Expense.status=='POSTED').scalar() or 0)
    active_members=db.query(Member).filter(Member.status=='ACTIVE').count()
    active_loans=db.query(Loan).filter(Loan.status.in_(['APPROVED','ACTIVE','RESTRUCTURED'])).count()
    requested_loans=db.query(Loan).filter(Loan.status=='REQUESTED').count()
    inst=db.query(LoanInstallment).filter(LoanInstallment.paid_at.is_(None),LoanInstallment.status.notin_(['PAID','AGREED'])).all()
    receivable=sum((max(ZERO,Decimal(i.amount or 0)-Decimal(i.paid_amount or 0)+Decimal(i.penalty_amount or 0)-Decimal(i.paid_penalty_amount or 0)) for i in inst),ZERO)
    coll=collections_summary(db,today)
    recovery=collection_recovery_summary(db)
    risk_rows=db.query(FinancialRiskAssessment).all()
    risk_counts={'PASS':0,'REVIEW':0,'BLOCKED':0}
    for r in risk_rows: risk_counts[r.status]=risk_counts.get(r.status,0)+1
    recon=db.query(FinancialReconciliation).order_by(FinancialReconciliation.created_at.desc()).first()
    ledger=verify_ledger_chain(db)
    result=Decimal('0')
    # Operational profitability proxy: realized interest + penalties - posted expenses.
    interest=Decimal(db.query(func.coalesce(func.sum(LoanInstallment.interest),0)).filter(LoanInstallment.paid_at.is_not(None)).scalar() or 0)
    penalties=Decimal(db.query(func.coalesce(func.sum(LoanInstallment.paid_penalty_amount),0)).scalar() or 0)
    result=interest+penalties-posted_exp
    flags=[]
    if cash < 0: flags.append('NEGATIVE_LOGICAL_CASH')
    if not ledger.get('status')=='PASS': flags.append('LEDGER_INTEGRITY')
    if not recon or recon.status!='PASS': flags.append('RECONCILIATION')
    if coll.get('overdue_installments',0)>0: flags.append('DELINQUENCY')
    if risk_counts['BLOCKED']>0: flags.append('RISK_BLOCKED')
    if recovery.get('promises_due_or_late',0)>0: flags.append('RECOVERY_ACTION_DUE')
    status='ATTENTION' if flags else 'PASS'
    return {'schema':'v0.50','snapshot_date':today.isoformat(),'status':status,'risk_flags':flags,
      'cash':{'logical_balance':money(cash),'credits':money(credits),'debits':money(debits)},
      'members':{'active':active_members},
      'contributions':{'paid_total':money(paid_contrib)},
      'loans':{'active_or_restructured':active_loans,'pending_requests':requested_loans,'receivable':money(receivable)},
      'collections':coll,'recovery':recovery,
      'risk':{'assessments':len(risk_rows),'pass':risk_counts['PASS'],'review':risk_counts['REVIEW'],'blocked':risk_counts['BLOCKED']},
      'profitability':{'interest_realized':money(interest),'penalties_realized':money(penalties),'posted_expenses':money(posted_exp),'operating_result_proxy':money(result)},
      'reconciliation':None if not recon else {'id':recon.id,'competence':recon.competence.isoformat(),'status':recon.status,'snapshot_hash':recon.snapshot_hash},
      'ledger_integrity':ledger}

def persist_executive_dashboard(db:Session, generated_by:int|None=None, today:date|None=None):
    today=today or date.today(); data=build_executive_dashboard(db,today)
    raw=json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    h=hashlib.sha256(raw.encode()).hexdigest()
    row=db.query(ExecutiveDashboardSnapshot).filter(ExecutiveDashboardSnapshot.snapshot_date==today).first()
    if row:
        row.status=data['status']; row.snapshot_json=raw; row.snapshot_hash=h; row.generated_by=generated_by
    else:
        row=ExecutiveDashboardSnapshot(snapshot_date=today,status=data['status'],snapshot_json=raw,snapshot_hash=h,generated_by=generated_by); db.add(row)
    db.flush(); return row,data
