from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.services.reconciliation_v032 import reconcile
from app.services.reconciliation_v040 import build_advanced_reconciliation, persist_reconciliation
from app.models import FinancialReconciliation

router = APIRouter(prefix='/admin/reconciliation', tags=['admin-reconciliation'])

@router.get('/run')
def run_reconciliation(admin=Depends(require_admin), db: Session = Depends(get_db)):
    return reconcile(db)


@router.get('/advanced')
def advanced(competence: date, admin=Depends(require_admin), db: Session = Depends(get_db)):
    return build_advanced_reconciliation(db, competence)

@router.post('/advanced/run')
def advanced_run(competence: date, admin=Depends(require_admin), db: Session = Depends(get_db)):
    row, result = persist_reconciliation(db, competence, admin.id)
    db.commit()
    return {'id': row.id, 'competence': row.competence.isoformat(), **result}

@router.get('/advanced/history')
def advanced_history(limit: int=50, admin=Depends(require_admin), db: Session = Depends(get_db)):
    rows=db.query(FinancialReconciliation).order_by(FinancialReconciliation.created_at.desc()).limit(min(limit,200)).all()
    return {'items':[{'id':r.id,'competence':r.competence.isoformat(),'status':r.status,'snapshot_hash':r.snapshot_hash,'created_at':r.created_at.isoformat()} for r in rows]}
