from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import ExecutiveRiskResponseSnapshot
from app.services.executive_risk_response_v077 import build_dashboard, persist_dashboard
router=APIRouter(prefix='/admin/executive-risk-response',tags=['executive-risk-response-v077'])
@router.get('')
def current(admin=Depends(require_admin),db:Session=Depends(get_db)): return build_dashboard(db)
@router.post('/snapshot')
def snapshot(admin=Depends(require_admin),db:Session=Depends(get_db)):
    row,data=persist_dashboard(db,admin.id); db.commit(); return {'id':row.id,'snapshot_hash':row.snapshot_hash,**data}
@router.get('/history')
def history(limit:int=Query(30,ge=1,le=200),admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=db.query(ExecutiveRiskResponseSnapshot).order_by(ExecutiveRiskResponseSnapshot.snapshot_date.desc()).limit(limit).all()
    return {'items':[{'id':r.id,'snapshot_date':r.snapshot_date.isoformat(),'status':r.status,'snapshot_hash':r.snapshot_hash} for r in rows]}
