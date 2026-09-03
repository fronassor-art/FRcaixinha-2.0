from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import ContinuousImprovementDashboardSnapshot
from app.services.continuous_improvement_dashboard_v084 import build_dashboard, persist_dashboard
router=APIRouter(prefix='/admin/continuous-improvement-dashboard',tags=['continuous-improvement-v084'])
@router.get('')
def current(admin=Depends(require_admin),db:Session=Depends(get_db)): return build_dashboard(db)
@router.post('/snapshot')
def snapshot(admin=Depends(require_admin),db:Session=Depends(get_db)):
    row,data=persist_dashboard(db,admin.id); db.commit(); return {'id':row.id,'snapshot_hash':row.snapshot_hash,**data}
@router.get('/history')
def history(limit:int=Query(30,ge=1,le=200),admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=db.query(ContinuousImprovementDashboardSnapshot).order_by(ContinuousImprovementDashboardSnapshot.snapshot_date.desc()).limit(limit).all()
    return {'items':[{'id':r.id,'snapshot_date':r.snapshot_date.isoformat(),'status':r.status,'snapshot_hash':r.snapshot_hash} for r in rows]}
@router.get('/snapshot/{id}')
def snapshot_detail(id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    row=db.get(ContinuousImprovementDashboardSnapshot,id)
    if not row: from fastapi import HTTPException; raise HTTPException(404,'snapshot_not_found')
    import json
    return {'id':row.id,'snapshot_hash':row.snapshot_hash,**json.loads(row.snapshot_json)}
