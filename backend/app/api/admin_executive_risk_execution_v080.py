from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import ExecutiveRiskDecisionExecution
from app.services.executive_risk_execution_v080 import create_execution, assign, start, complete, verify
router=APIRouter(prefix='/admin/executive-risk-execution', tags=['executive-risk-execution-v080'])
class AssignBody(BaseModel): assigned_to:int=Field(gt=0)
class CompleteBody(BaseModel): evidence_note:str=Field(min_length=1); resolution:str=Field(min_length=1)
class VerifyBody(BaseModel): note:str=Field(min_length=1)
@router.post('')
def create(governance_id:int, assigned_to:int|None=None, admin=Depends(require_admin), db:Session=Depends(get_db)):
    try: r=create_execution(db,governance_id,admin.id,assigned_to); db.commit(); return {'id':r.id,'status':r.status,'execution_hash':r.execution_hash}
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
@router.get('')
def listing(status:str|None=None, limit:int=Query(100,ge=1,le=500), admin=Depends(require_admin), db:Session=Depends(get_db)):
    q=db.query(ExecutiveRiskDecisionExecution).order_by(ExecutiveRiskDecisionExecution.created_at.desc())
    if status:q=q.filter(ExecutiveRiskDecisionExecution.status==status.upper())
    rows=q.limit(limit).all()
    return {'items':[{'id':r.id,'governance_id':r.governance_id,'status':r.status,'assigned_to':r.assigned_to,'started_by':r.started_by,'started_at':r.started_at,'completed_by':r.completed_by,'completed_at':r.completed_at,'verified_by':r.verified_by,'verified_at':r.verified_at,'execution_hash':r.execution_hash} for r in rows]}
def _get(db,i):
    r=db.get(ExecutiveRiskDecisionExecution,i)
    if not r: raise HTTPException(404,'execution_not_found')
    return r
@router.post('/{id}/assign')
def assign_route(id:int, body:AssignBody, admin=Depends(require_admin), db:Session=Depends(get_db)):
    try:r=assign(db,_get(db,id),admin.id,body.assigned_to);db.commit();return {'id':r.id,'status':r.status,'assigned_to':r.assigned_to,'execution_hash':r.execution_hash}
    except ValueError as e:db.rollback();raise HTTPException(400,str(e))
@router.post('/{id}/start')
def start_route(id:int, admin=Depends(require_admin), db:Session=Depends(get_db)):
    try:r=start(db,_get(db,id),admin.id);db.commit();return {'id':r.id,'status':r.status,'execution_hash':r.execution_hash}
    except ValueError as e:db.rollback();raise HTTPException(400,str(e))
@router.post('/{id}/complete')
def complete_route(id:int, body:CompleteBody, admin=Depends(require_admin), db:Session=Depends(get_db)):
    try:r=complete(db,_get(db,id),admin.id,body.evidence_note,body.resolution);db.commit();return {'id':r.id,'status':r.status,'execution_hash':r.execution_hash}
    except ValueError as e:db.rollback();raise HTTPException(400,str(e))
@router.post('/{id}/verify')
def verify_route(id:int, body:VerifyBody, admin=Depends(require_admin), db:Session=Depends(get_db)):
    try:r=verify(db,_get(db,id),admin.id,body.note);db.commit();return {'id':r.id,'status':r.status,'execution_hash':r.execution_hash}
    except ValueError as e:db.rollback();raise HTTPException(400,str(e))
