from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import OperationalRiskTrendSnapshot
from app.services.operational_risk_v074 import calculate_risk, persist_risk_snapshot
router=APIRouter(prefix='/admin/operational-risk',tags=['operational-risk-v074'])
@router.get('')
def current(admin=Depends(require_admin),db:Session=Depends(get_db)): return calculate_risk(db)
@router.post('/snapshot')
def snapshot(admin=Depends(require_admin),db:Session=Depends(get_db)):
    row,data=persist_risk_snapshot(db,generated_by=admin.id); db.commit(); return {'id':row.id,**data,'snapshot_hash':row.snapshot_hash}
@router.get('/history')
def history(admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=db.query(OperationalRiskTrendSnapshot).order_by(OperationalRiskTrendSnapshot.snapshot_date.desc()).limit(90).all()
    return {'items':[{'id':r.id,'snapshot_date':r.snapshot_date.isoformat(),'status':r.status,'risk_score':r.risk_score,'snapshot_hash':r.snapshot_hash} for r in rows]}
