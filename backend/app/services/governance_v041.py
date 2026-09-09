import hashlib, json
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models import (GovernanceSnapshot, Member, Contribution, Loan, LoanInstallment,
                        CollectionAgreement, WebhookEvent, AuditLog, FinancialReconciliation, LedgerEntry)
from app.services.ledger import verify_ledger_chain
from app.services.collections_v038 import collections_summary


def money(v):
    return str(Decimal(v or 0).quantize(Decimal('0.01')))


def _latest_reconciliation(db):
    return db.query(FinancialReconciliation).order_by(FinancialReconciliation.created_at.desc()).first()


def build_executive_governance(db: Session, today: date | None = None):
    today = today or date.today()
    recon = _latest_reconciliation(db)
    ledger = verify_ledger_chain(db)
    coll = collections_summary(db)
    active_members = db.query(Member).filter(Member.status == 'ACTIVE').count()
    paid_contrib = Decimal(db.query(func.coalesce(func.sum(Contribution.amount),0)).filter(Contribution.status=='PAID').scalar() or 0)
    pending_loans = db.query(Loan).filter(Loan.status=='REQUESTED').count()
    active_loans = db.query(Loan).filter(Loan.status.in_(['APPROVED','ACTIVE','RESTRUCTURED'])).count()
    open_installments = db.query(LoanInstallment).filter(LoanInstallment.status!='PAID').count()
    pending_agreements = db.query(CollectionAgreement).filter(CollectionAgreement.status=='REQUESTED').count()
    webhook_pending = db.query(WebhookEvent).filter(WebhookEvent.processed==False).count()  # noqa
    audit_24h = db.query(AuditLog).filter(AuditLog.created_at >= datetime.now(timezone.utc) - timedelta(days=1)).count()
    credits = Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount),0)).filter(LedgerEntry.direction=='CREDIT').scalar() or 0)
    debits = Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount),0)).filter(LedgerEntry.direction=='DEBIT').scalar() or 0)
    balance = credits-debits
    checks = {
      'ledger_integrity': ledger.get('status') == 'PASS',
      'reconciliation_latest': recon is not None and recon.status == 'PASS',
      'webhooks_clear': webhook_pending == 0,
      'no_negative_cash': balance >= 0,
    }
    risk_flags=[]
    if not checks['ledger_integrity']: risk_flags.append('LEDGER_INTEGRITY')
    if not checks['reconciliation_latest']: risk_flags.append('RECONCILIATION')
    if not checks['webhooks_clear']: risk_flags.append('WEBHOOK_BACKLOG')
    if not checks['no_negative_cash']: risk_flags.append('NEGATIVE_LOGICAL_CASH')
    if coll.get('overdue_installments',0)>0: risk_flags.append('DELINQUENCY')
    if pending_agreements>0: risk_flags.append('AGREEMENTS_PENDING')
    status='ATTENTION' if risk_flags else 'PASS'
    return {
      'schema':'v0.41','snapshot_date':today.isoformat(),'status':status,
      'risk_flags':risk_flags,
      'checks':checks,
      'members':{'active':active_members},
      'contributions':{'paid_total':money(paid_contrib)},
      'loans':{'pending_requests':pending_loans,'active_or_restructured':active_loans,'open_installments':open_installments},
      'collections':coll,
      'agreements':{'pending':pending_agreements},
      'webhooks':{'pending':webhook_pending},
      'audit':{'events_last_24h':audit_24h},
      'ledger':{'credits':money(credits),'debits':money(debits),'balance':money(balance),'integrity':ledger},
      'latest_reconciliation':None if not recon else {'id':recon.id,'competence':recon.competence.isoformat(),'status':recon.status,'snapshot_hash':recon.snapshot_hash},
    }


def persist_governance_snapshot(db: Session, generated_by: int | None = None, today: date | None = None):
    today=today or date.today(); data=build_executive_governance(db,today)
    raw=json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    h=hashlib.sha256(raw).hexdigest()
    row=db.query(GovernanceSnapshot).filter(GovernanceSnapshot.snapshot_date==today).first()
    if row:
        row.status=data['status']; row.snapshot_json=raw.decode(); row.snapshot_hash=h; row.generated_by=generated_by
    else:
        row=GovernanceSnapshot(snapshot_date=today,status=data['status'],snapshot_json=raw.decode(),snapshot_hash=h,generated_by=generated_by)
        db.add(row)
    db.flush()
    return row,data
