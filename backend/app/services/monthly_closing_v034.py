import hashlib, json
from calendar import monthrange
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models import Contribution, Expense, LoanInstallment, LedgerEntry, MonthlyClosing
from app.services.reconciliation_v032 import reconcile

CENT = Decimal('0.01')
def money(v): return str(Decimal(v or 0).quantize(CENT, rounding=ROUND_HALF_UP))
def bounds(d): return d.replace(day=1), d.replace(day=monthrange(d.year,d.month)[1])
def ledger_balance(db):
    c=Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount),0)).filter(LedgerEntry.direction=='CREDIT').scalar() or 0)
    d=Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount),0)).filter(LedgerEntry.direction=='DEBIT').scalar() or 0)
    return (c-d).quantize(CENT)

def build_snapshot(db: Session, competence: date):
    a,b=bounds(competence)
    contrib=Decimal(db.query(func.coalesce(func.sum(Contribution.amount),0)).filter(Contribution.status=='PAID',Contribution.competence.between(a,b)).scalar() or 0)
    exp=Decimal(db.query(func.coalesce(func.sum(Expense.amount),0)).filter(Expense.status=='POSTED',Expense.expense_date.between(a,b)).scalar() or 0)
    ints=sum((Decimal(i.interest) for i in db.query(LoanInstallment).filter(LoanInstallment.status=='PAID',LoanInstallment.paid_at>=datetime.combine(a,datetime.min.time(),tzinfo=timezone.utc),LoanInstallment.paid_at<datetime.combine(b,datetime.min.time(),tzinfo=timezone.utc).replace(day=b.day)+__import__('datetime').timedelta(days=1))), Decimal('0'))
    credits=Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount),0)).filter(LedgerEntry.direction=='CREDIT',LedgerEntry.created_at>=datetime.combine(a,datetime.min.time(),tzinfo=timezone.utc),LedgerEntry.created_at<datetime.combine(b,datetime.min.time(),tzinfo=timezone.utc)+__import__('datetime').timedelta(days=1)).scalar() or 0)
    debits=Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount),0)).filter(LedgerEntry.direction=='DEBIT',LedgerEntry.created_at>=datetime.combine(a,datetime.min.time(),tzinfo=timezone.utc),LedgerEntry.created_at<datetime.combine(b,datetime.min.time(),tzinfo=timezone.utc)+__import__('datetime').timedelta(days=1)).scalar() or 0)
    snap={'schema':'v0.34','competence':a.isoformat(),'period_end':b.isoformat(),'contributions_paid':money(contrib),'expenses_posted':money(exp),'interest_received':money(ints),'ledger_credits_in_period':money(credits),'ledger_debits_in_period':money(debits),'ledger_balance_at_close':money(ledger_balance(db))}
    raw=json.dumps(snap,sort_keys=True,separators=(',',':')).encode()
    return snap, hashlib.sha256(raw).hexdigest()

def close_month(db: Session, competence: date, admin_id: int):
    competence=competence.replace(day=1)
    existing=db.query(MonthlyClosing).filter(MonthlyClosing.competence==competence).with_for_update().first()
    if existing and existing.status=='CLOSED': raise ValueError('Competência já encerrada.')
    recon=reconcile(db)
    if recon['status']!='PASS': raise ValueError('Reconciliação financeira deve estar PASS antes do fechamento.')
    snap,h=build_snapshot(db,competence)
    if not existing: existing=MonthlyClosing(competence=competence); db.add(existing); db.flush()
    existing.status='CLOSED'; existing.total_contributions=Decimal(snap['contributions_paid']); existing.total_expenses=Decimal(snap['expenses_posted']); existing.total_interest_received=Decimal(snap['interest_received']); existing.ledger_balance=Decimal(snap['ledger_balance_at_close']); existing.closed_by=admin_id; existing.closed_at=datetime.now(timezone.utc); existing.snapshot_json=json.dumps(snap,sort_keys=True,separators=(',',':')); existing.snapshot_hash=h
    return existing, snap, h
