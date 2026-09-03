from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import ContinuousImprovementAuditSnapshot, ContinuousImprovementExecution
from app.services.continuous_improvement_audit_v091 import persist, build_cycle, verify_snapshot
router=APIRouter(prefix='/admin/continuous-improvement-audit',tags=['continuous-improvement-v091'])
@router.get('/executions/{execution_id}')
def current(execution_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    if not db.get(ContinuousImprovementExecution,execution_id): raise HTTPException(404,'execution_not_found')
    return build_cycle(db,execution_id)
@router.post('/executions/{execution_id}/snapshot')
def create(execution_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: row,data=persist(db,execution_id,admin.id); db.commit()
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
    return {'id':row.id,'execution_id':row.execution_id,'status':row.status,'snapshot_hash':row.snapshot_hash,'created_at':row.created_at.isoformat()}
@router.post('/executions/{execution_id}/verify')
def verify_current(execution_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    if not db.get(ContinuousImprovementExecution,execution_id): raise HTTPException(404,'execution_not_found')
    return build_cycle(db,execution_id)
@router.get('/snapshots')
def listing(execution_id:int|None=None,status:str|None=None,limit:int=Query(50,ge=1,le=200),admin=Depends(require_admin),db:Session=Depends(get_db)):
    q=db.query(ContinuousImprovementAuditSnapshot).order_by(ContinuousImprovementAuditSnapshot.id.desc())
    if execution_id is not None:q=q.filter_by(execution_id=execution_id)
    if status:q=q.filter_by(status=status)
    rows=q.limit(limit).all()
    return {'items':[{'id':r.id,'execution_id':r.execution_id,'status':r.status,'snapshot_hash':r.snapshot_hash,'generated_by':r.generated_by,'created_at':r.created_at.isoformat()} for r in rows]}
@router.get('/snapshots/{snapshot_id}')
def detail(snapshot_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    r=db.get(ContinuousImprovementAuditSnapshot,snapshot_id)
    if not r: raise HTTPException(404,'snapshot_not_found')
    import json
    return {'id':r.id,'execution_id':r.execution_id,'status':r.status,'snapshot_hash':r.snapshot_hash,'generated_by':r.generated_by,'created_at':r.created_at.isoformat(),'snapshot':json.loads(r.snapshot_json)}
@router.get('/snapshots/{snapshot_id}/verify')
def verify(snapshot_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try:return verify_snapshot(db,snapshot_id)
    except ValueError as e: raise HTTPException(404,str(e))
