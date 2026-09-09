from __future__ import annotations
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import AllocationDecisionRecord, AllocationTransparencySnapshot
from app.services.allocation_decision_governance_v057 import create_decision, decision_hash

router=APIRouter(prefix='/admin/allocation-decisions', tags=['allocation-decisions-v057'])

def row(r):
    return {'id':r.id,'transparency_snapshot_id':r.transparency_snapshot_id,'group_id':r.group_id,'requested_by':r.requested_by,
            'analyzed_by':r.analyzed_by,'decided_by':r.decided_by,'decision':r.decision,'policy_version':r.policy_version,
            'transparency_hash':r.transparency_hash,'decision_input_hash':r.decision_input_hash,'exception_applied':r.exception_applied,
            'exception_reason':r.exception_reason,'admin_note':r.admin_note,'created_at':r.created_at.isoformat()}

@router.post('/{snapshot_id}')
def decide(snapshot_id:int, payload:dict, admin=Depends(require_admin), db:Session=Depends(get_db)):
    snapshot=db.get(AllocationTransparencySnapshot,snapshot_id)
    if not snapshot: raise HTTPException(404,'Snapshot de transparência não encontrado.')
    existing=db.query(AllocationDecisionRecord).filter(AllocationDecisionRecord.transparency_snapshot_id==snapshot_id).first()
    if existing: raise HTTPException(409,'Este snapshot já possui uma decisão governada.')
    try:
        r,h=create_decision(db,snapshot=snapshot,actor_id=admin.id,decision=str(payload.get('decision','')).upper(),
                            requested_by=payload.get('requested_by'),exception_applied=bool(payload.get('exception_applied',False)),
                            exception_reason=payload.get('exception_reason'),admin_note=payload.get('admin_note'))
        db.flush()
        db.query(__import__('app.models',fromlist=['AuditLog']).AuditLog).filter_by(entity_type='ALLOCATION_DECISION',entity_id='pending',details=h).update({"entity_id":str(r.id)})
        db.commit(); db.refresh(r); return row(r)
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))

@router.get('/history')
def history(group_id:int|None=None, decision:str|None=None, limit:int=Query(50,ge=1,le=200), admin=Depends(require_admin), db:Session=Depends(get_db)):
    q=db.query(AllocationDecisionRecord).order_by(AllocationDecisionRecord.created_at.desc())
    if group_id is not None: q=q.filter(AllocationDecisionRecord.group_id==group_id)
    if decision: q=q.filter(AllocationDecisionRecord.decision==decision.upper())
    return {'items':[row(x) for x in q.limit(limit).all()]}

@router.get('/{decision_id}')
def detail(decision_id:int, admin=Depends(require_admin), db:Session=Depends(get_db)):
    r=db.get(AllocationDecisionRecord,decision_id)
    if not r: raise HTTPException(404,'Decisão não encontrada.')
    expected=decision_hash(snapshot_id=r.transparency_snapshot_id,transparency_hash=r.transparency_hash,policy_version=r.policy_version,
                           decision=r.decision,exception_applied=r.exception_applied,exception_reason=r.exception_reason,admin_note=r.admin_note)
    if expected != r.decision_input_hash: raise HTTPException(409,'Integridade da decisão comprometida.')
    return {'decision':row(r),'transparency':json.loads(db.get(AllocationTransparencySnapshot,r.transparency_snapshot_id).explanation_json)}
