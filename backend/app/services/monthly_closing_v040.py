import hashlib, json
from calendar import monthrange
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy.orm import Session
from app.models import MonthlyClosing
from app.services.reconciliation_v040 import build_advanced_reconciliation, dt_start, dt_end

CENT=Decimal("0.01")
def money(v): return str(Decimal(v or 0).quantize(CENT, rounding=ROUND_HALF_UP))

def close_month_v040(db: Session, competence: date, admin_id: int):
    competence=competence.replace(day=1)
    existing=db.query(MonthlyClosing).filter(MonthlyClosing.competence==competence).with_for_update().first()
    if existing and existing.status=='CLOSED': raise ValueError('Competência já encerrada.')
    recon=build_advanced_reconciliation(db, competence)
    if recon['status']!='PASS': raise ValueError('Reconciliação avançada deve estar PASS antes do fechamento.')
    snap=dict(recon['snapshot'])
    snap['closing_schema']='v0.40'
    snap['reconciliation_hash']=recon['snapshot_hash']
    raw=json.dumps(snap,sort_keys=True,separators=(',',':')).encode(); h=hashlib.sha256(raw).hexdigest()
    if not existing: existing=MonthlyClosing(competence=competence); db.add(existing); db.flush()
    existing.status='CLOSED'; existing.total_contributions=Decimal(snap['contributions_paid']); existing.total_expenses=Decimal(snap['expenses_posted']); existing.total_interest_received=Decimal(snap['loan_payments_ledger'])+Decimal(snap['agreement_payments_ledger']); existing.ledger_balance=Decimal(snap['ledger_net']); existing.closed_by=admin_id; existing.closed_at=datetime.now(timezone.utc); existing.snapshot_json=json.dumps(snap,sort_keys=True,separators=(',',':')); existing.snapshot_hash=h
    return existing,snap,h
