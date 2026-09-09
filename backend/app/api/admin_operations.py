from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db
from app.models import Payment, WebhookEvent, LoanInstallment, LedgerEntry, AuditLog
from app.services.reconciliation_v032 import reconcile

router = APIRouter(prefix='/admin/operations', tags=['admin-operations'])

def money(v):
    return str(Decimal(v or 0).quantize(Decimal('0.01')))

@router.get('/dashboard')
def operations_dashboard(admin=Depends(require_admin), db: Session = Depends(get_db)):
    rec = reconcile(db)
    credits = Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount), 0)).filter(LedgerEntry.direction=='CREDIT').scalar() or 0)
    debits = Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount), 0)).filter(LedgerEntry.direction=='DEBIT').scalar() or 0)
    approved = Decimal(db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.status=='approved').scalar() or 0)
    posted = Decimal(db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.status=='approved', Payment.ledger_posted_at.is_not(None)).scalar() or 0)
    pending_webhooks = db.query(WebhookEvent).filter(WebhookEvent.processed == False).count()  # noqa: E712
    open_installments = db.query(LoanInstallment).filter(LoanInstallment.status != 'PAID').count()
    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'reconciliation': rec,
        'ledger': {'credits': money(credits), 'debits': money(debits), 'balance': money(credits-debits)},
        'payments': {'approved': money(approved), 'ledger_posted': money(posted), 'unposted_amount': money(approved-posted)},
        'webhooks': {'pending': pending_webhooks},
        'installments': {'open': open_installments},
        'go_no_go': 'GO' if rec['status'] == 'PASS' and pending_webhooks == 0 and approved == posted else 'NO-GO',
    }

@router.post('/reconciliation/audit')
def audit_reconciliation(admin=Depends(require_admin), db: Session = Depends(get_db)):
    result = reconcile(db)
    db.add(AuditLog(actor_user_id=admin.id, action='RECONCILIATION_RUN', entity_type='OPERATIONS', entity_id='dashboard', details=result['status']))
    db.commit()
    return result
