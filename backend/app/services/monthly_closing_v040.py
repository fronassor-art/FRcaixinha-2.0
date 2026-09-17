import hashlib, json
from calendar import monthrange
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy.orm import Session
from app.models import MonthlyClosing
from app.services.reconciliation_v040 import build_advanced_reconciliation, dt_start, dt_end
from app.services.monthly_closing_guard import ensure_monthly_closing_open

CENT=Decimal("0.01")
def money(v): return str(Decimal(v or 0).quantize(CENT, rounding=ROUND_HALF_UP))

def canonical_snapshot_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))

def snapshot_digest(value):
    return hashlib.sha256(canonical_snapshot_json(value).encode('utf-8')).hexdigest()

def close_month_v040(db: Session, competence: date, admin_id: int):
    competence=competence.replace(day=1)
    existing=db.query(MonthlyClosing).filter(MonthlyClosing.competence==competence).with_for_update().first()
    ensure_monthly_closing_open(existing)
    recon=build_advanced_reconciliation(db, competence)
    if recon['status']!='PASS': raise ValueError('Reconciliação avançada deve estar PASS antes do fechamento.')
    snap=dict(recon['snapshot'])
    snap['closing_schema']='v0.40'
    snap['reconciliation_hash']=recon['snapshot_hash']
    h=snapshot_digest(snap)
    if not existing: existing=MonthlyClosing(competence=competence); db.add(existing); db.flush()
    existing.status='CLOSED'; existing.total_contributions=Decimal(snap['contributions_paid']); existing.total_expenses=Decimal(snap['expenses_posted']); existing.total_interest_received=Decimal(snap['interest_received']); existing.ledger_balance=Decimal(snap['ledger_net']); existing.closed_by=admin_id; existing.closed_at=datetime.now(timezone.utc); existing.snapshot_json=canonical_snapshot_json(snap); existing.snapshot_hash=h
    return existing,snap,h
