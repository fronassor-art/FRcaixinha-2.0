from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import ContinuousImprovementExecutiveAuditSnapshot
from app.services.continuous_improvement_executive_audit_v092 import persist_report, build_report, verify_report
router=APIRouter(prefix='/admin/continuous-improvement-executive-audit',tags=['continuous-improvement-v092'])
@router.get('/current')
def current(admin=Depends(require_admin),db:Session=Depends(get_db)): return build_report(db)
@router.post('/snapshot')
def create(admin=Depends(require_admin),db:Session=Depends(get_db)):
    row,data=persist_report(db,admin.id); db.commit(); return {'id':row.id,'status':row.status,'snapshot_hash':row.snapshot_hash,'created_at':row.created_at.isoformat()}
@router.post('/sync')
def sync(admin=Depends(require_admin),db:Session=Depends(get_db)):
    row,data=persist_report(db,admin.id); db.commit(); return {'id':row.id,'status':row.status,'snapshot_hash':row.snapshot_hash}
@router.get('/history')
def history(limit:int=Query(50,ge=1,le=200),admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=db.query(ContinuousImprovementExecutiveAuditSnapshot).order_by(ContinuousImprovementExecutiveAuditSnapshot.id.desc()).limit(limit).all()
    return {'items':[{'id':r.id,'status':r.status,'snapshot_hash':r.snapshot_hash,'generated_by':r.generated_by,'created_at':r.created_at.isoformat()} for r in rows]}
@router.get('/snapshots/{snapshot_id}')
def detail(snapshot_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    r=db.get(ContinuousImprovementExecutiveAuditSnapshot,snapshot_id)
    if not r: raise HTTPException(404,'snapshot_not_found')
    import json
    return {'id':r.id,'status':r.status,'snapshot_hash':r.snapshot_hash,'snapshot':json.loads(r.snapshot_json)}
@router.get('/snapshots/{snapshot_id}/verify')
def verify(snapshot_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try:return verify_report(db,snapshot_id)
    except ValueError as e: raise HTTPException(404,str(e))
