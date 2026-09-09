from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import ContinuousImprovementExecution
from app.services.continuous_improvement_execution_v088 import create_from_decision, accept_execution, complete_execution, verify_execution
router=APIRouter(prefix='/admin/continuous-improvement-execution',tags=['continuous-improvement-v088'])
class CompleteIn(BaseModel):
    resolution_note:str=Field(min_length=3,max_length=4000)
    evidence_note:str=Field(min_length=3,max_length=4000)
class VerifyIn(BaseModel):
    note:str=Field(min_length=3,max_length=4000)
@router.post('/from-decision/{decision_id}')
def from_decision(decision_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: row=create_from_decision(db,decision_id,admin.id); db.commit()
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
    return {'id':row.id,'status':row.status,'assigned_to':row.assigned_to,'execution_hash':row.execution_hash}
@router.post('/{execution_id}/start')
def start(execution_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: row=accept_execution(db,execution_id,admin.id); db.commit()
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
    return {'id':row.id,'status':row.status,'started_at':row.started_at.isoformat(),'execution_hash':row.execution_hash}
@router.post('/{execution_id}/complete')
def complete(execution_id:int,body:CompleteIn,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: row=complete_execution(db,execution_id,admin.id,body.resolution_note,body.evidence_note); db.commit()
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
    return {'id':row.id,'status':row.status,'completed_at':row.completed_at.isoformat(),'execution_hash':row.execution_hash}
@router.post('/{execution_id}/verify')
def verify(execution_id:int,body:VerifyIn,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: row=verify_execution(db,execution_id,admin.id,body.note); db.commit()
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
    return {'id':row.id,'status':row.status,'verified_by':row.verified_by,'verified_at':row.verified_at.isoformat(),'execution_hash':row.execution_hash}
@router.get('')
def listing(status:str|None=None,limit:int=Query(50,ge=1,le=200),admin=Depends(require_admin),db:Session=Depends(get_db)):
    q=db.query(ContinuousImprovementExecution).order_by(ContinuousImprovementExecution.id.desc())
    if status: q=q.filter_by(status=status)
    rows=q.limit(limit).all()
    return {'items':[{'id':r.id,'decision_id':r.decision_id,'recommendation_id':r.recommendation_id,'plan_id':r.plan_id,'status':r.status,'assigned_to':r.assigned_to,'verified_by':r.verified_by,'execution_hash':r.execution_hash} for r in rows]}
@router.get('/{execution_id}')
def detail(execution_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    r=db.get(ContinuousImprovementExecution,execution_id)
    if not r: raise HTTPException(404,'execution_not_found')
    return {'id':r.id,'decision_id':r.decision_id,'recommendation_id':r.recommendation_id,'plan_id':r.plan_id,'status':r.status,'assigned_to':r.assigned_to,'started_at':r.started_at.isoformat() if r.started_at else None,'completed_at':r.completed_at.isoformat() if r.completed_at else None,'resolution_note':r.resolution_note,'evidence_note':r.evidence_note,'verified_by':r.verified_by,'verified_at':r.verified_at.isoformat() if r.verified_at else None,'verification_note':r.verification_note,'execution_hash':r.execution_hash}
