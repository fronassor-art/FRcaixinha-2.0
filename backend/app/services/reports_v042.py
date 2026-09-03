from calendar import monthrange
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import csv, hashlib, io, json
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models import (User, Member, Contribution, Loan, LoanInstallment, LedgerEntry,
                        Expense, CollectionAgreement, AgreementInstallment, ReportSnapshot)
from app.services.collections_v038 import collections_summary

ZERO=Decimal('0.00')
def money(v): return str(Decimal(v or 0).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
def bounds(d): return d.replace(day=1), d.replace(day=monthrange(d.year,d.month)[1])
def next_day(d): return date.fromordinal(d.toordinal()+1)

def _period_ledger(db,start,end):
    c=Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount),0)).filter(LedgerEntry.direction=='CREDIT',LedgerEntry.created_at>=start).filter(LedgerEntry.created_at<datetime.combine(next_day(end),datetime.min.time(),tzinfo=timezone.utc)).scalar() or 0)
    de=Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount),0)).filter(LedgerEntry.direction=='DEBIT',LedgerEntry.created_at>=start).filter(LedgerEntry.created_at<datetime.combine(next_day(end),datetime.min.time(),tzinfo=timezone.utc)).scalar() or 0)
    return c,de

def monthly_accountability(db:Session, competence:date):
    start,end=bounds(competence)
    contrib=Decimal(db.query(func.coalesce(func.sum(Contribution.amount),0)).filter(Contribution.status=='PAID',Contribution.competence.between(start,end)).scalar() or 0)
    expenses=Decimal(db.query(func.coalesce(func.sum(Expense.amount),0)).filter(Expense.status=='POSTED',Expense.expense_date.between(start,end)).scalar() or 0)
    inst=db.query(LoanInstallment).filter(LoanInstallment.paid_at!=None,LoanInstallment.paid_at>=datetime.combine(start,datetime.min.time(),tzinfo=timezone.utc),LoanInstallment.paid_at<datetime.combine(next_day(end),datetime.min.time(),tzinfo=timezone.utc)).all()  # noqa
    loan_principal=sum((Decimal(i.principal or 0) for i in inst),ZERO)
    interest=sum((Decimal(i.interest or 0) for i in inst),ZERO)
    penalties=sum((Decimal(i.paid_penalty_amount or 0) for i in inst),ZERO)
    acinst=db.query(AgreementInstallment).filter(AgreementInstallment.paid_at!=None,AgreementInstallment.paid_at>=datetime.combine(start,datetime.min.time(),tzinfo=timezone.utc),AgreementInstallment.paid_at<datetime.combine(next_day(end),datetime.min.time(),tzinfo=timezone.utc)).all()  # noqa
    agreement_paid=sum((Decimal(i.paid_amount or 0) for i in acinst),ZERO)
    credits,debits=_period_ledger(db,start,end)
    coll=collections_summary(db,end)
    return {'schema':'v0.42','report_type':'MONTHLY','competence':start.isoformat(),'period_end':end.isoformat(),
      'inflows':{'contributions_paid':money(contrib),'loan_principal_received':money(loan_principal),'interest_received':money(interest),'penalties_received':money(penalties),'agreement_installments_received':money(agreement_paid)},
      'expenses':money(expenses),'operating_result':money(contrib+interest+penalties-expenses),
      'ledger':{'credits':money(credits),'debits':money(debits),'net':money(credits-debits)},
      'collections':{'overdue_installments':coll.get('overdue_installments',0),'overdue_balance':coll.get('overdue_balance','0.00')},
      'loans':{'active':db.query(Loan).filter(Loan.status.in_(['APPROVED','ACTIVE','RESTRUCTURED'])).count()},
    }

def annual_accountability(db:Session, year:int):
    rows=[]
    for m in range(1,13): rows.append(monthly_accountability(db,date(year,m,1)))
    def dec(path): return sum((Decimal(r[path[0]][path[1]]) for r in rows),ZERO)
    return {'schema':'v0.42','report_type':'ANNUAL','year':year,'months':rows,
      'totals':{'contributions_paid':money(dec(('inflows','contributions_paid'))),'interest_received':money(dec(('inflows','interest_received'))),'penalties_received':money(dec(('inflows','penalties_received'))),'expenses':money(sum((Decimal(r['expenses']) for r in rows),ZERO)),'operating_result':money(sum((Decimal(r['operating_result']) for r in rows),ZERO))}}

def participant_performance(db:Session, member_id:int, competence:date|None=None):
    m=db.get(Member,member_id)
    if not m:return None
    u=db.get(User,m.user_id)
    contrib=db.query(Contribution).filter(Contribution.member_id==member_id,Contribution.status=='PAID').all()
    loans=db.query(Loan).filter(Loan.member_id==member_id).all()
    ids=[x.id for x in loans]
    inst=db.query(LoanInstallment).filter(LoanInstallment.loan_id.in_(ids)).all() if ids else []
    paid=sum((Decimal(i.paid_amount or 0) for i in inst),ZERO)
    outstanding=sum((max(ZERO,Decimal(i.amount or 0)-Decimal(i.paid_amount or 0)) for i in inst if i.status!='PAID'),ZERO)
    overdue=sum((max(ZERO,Decimal(i.amount or 0)-Decimal(i.paid_amount or 0)) for i in inst if i.status!='PAID' and i.due_date < date.today()),ZERO)
    ontime=sum(1 for i in inst if i.paid_at and i.paid_at.date()<=i.due_date)
    paid_count=sum(1 for i in inst if i.status=='PAID')
    return {'schema':'v0.42','report_type':'PARTICIPANT','competence':competence.isoformat() if competence else None,'member':{'id':m.id,'name':u.name,'status':m.status},'metrics':{'contributions_paid':money(sum((Decimal(c.amount) for c in contrib),ZERO)),'loans_count':len(loans),'loan_payments':money(paid),'loan_outstanding':money(outstanding),'overdue_balance':money(overdue),'on_time_ratio':round(ontime/paid_count,4) if paid_count else None}}

def persist_report(db, report_type, competence, payload, generated_by=None, scope_id=None):
    raw=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False); h=hashlib.sha256(raw.encode()).hexdigest()
    row=db.query(ReportSnapshot).filter(ReportSnapshot.report_type==report_type,ReportSnapshot.competence==competence,ReportSnapshot.scope_id==scope_id).first()
    if row: row.snapshot_json=raw;row.snapshot_hash=h;row.generated_by=generated_by
    else: row=ReportSnapshot(report_type=report_type,competence=competence,scope_id=scope_id,snapshot_json=raw,snapshot_hash=h,generated_by=generated_by);db.add(row)
    db.flush();return row

def csv_text(report):
    out=io.StringIO(); w=csv.writer(out)
    if report.get('report_type')=='ANNUAL':
        w.writerow(['competence','contributions_paid','interest_received','penalties_received','expenses','operating_result'])
        for r in report['months']: w.writerow([r['competence'],r['inflows']['contributions_paid'],r['inflows']['interest_received'],r['inflows']['penalties_received'],r['expenses'],r['operating_result']])
    else:
        w.writerow(['metric','value'])
        def walk(d,p=''):
            for k,v in d.items():
                if isinstance(v,dict): walk(v,f'{p}{k}.')
                elif not isinstance(v,list): w.writerow([f'{p}{k}',v])
        walk(report)
    return out.getvalue()
