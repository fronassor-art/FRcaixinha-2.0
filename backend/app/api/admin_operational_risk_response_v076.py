from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import OperationalRiskResponsePlan
from app.services.operational_risk_response_v076 import sync_response_plans, assign_plan, verify_plan
router=APIRouter(prefix='/admin/operational-risk-response',tags=['operational-risk-response-v076'])
class AssignIn(BaseModel): assigned_to:int
class VerifyIn(BaseModel): evidence_note:str; resolution:str
@router.post('/sync')
def sync(admin=Depends(require_admin),db:Session=Depends(get_db)):
    out=sync_response_plans(db,admin.id); db.commit(); return out
@router.get('')
def listing(status:str|None=None,admin=Depends(require_admin),db:Session=Depends(get_db)):
    q=db.query(OperationalRiskResponsePlan).order_by(OperationalRiskResponsePlan.created_at.desc())
    if status:q=q.filter(OperationalRiskResponsePlan.status==status.upper())
    rows=q.limit(200).all(); return {'items':[{'id':p.id,'alert_id':p.alert_id,'status':p.status,'priority':p.priority,'assigned_to':p.assigned_to,'workflow_task_id':p.workflow_task_id,'due_at':p.due_at.isoformat() if p.due_at else None,'plan':p.plan,'evidence_note':p.evidence_note,'resolution':p.resolution} for p in rows]}
@router.post('/{plan_id}/assign')
def assign(plan_id:int,body:AssignIn,admin=Depends(require_admin),db:Session=Depends(get_db)):
    p=db.get(OperationalRiskResponsePlan,plan_id)
    if not p: raise HTTPException(404,'Plano não encontrado.')
    assign_plan(db,p,admin.id,body.assigned_to); db.commit(); return {'id':p.id,'status':p.status,'assigned_to':p.assigned_to}
@router.post('/{plan_id}/verify')
def verify(plan_id:int,body:VerifyIn,admin=Depends(require_admin),db:Session=Depends(get_db)):
    p=db.get(OperationalRiskResponsePlan,plan_id)
    if not p: raise HTTPException(404,'Plano não encontrado.')
    try: verify_plan(db,p,admin.id,body.evidence_note,body.resolution); db.commit(); return {'id':p.id,'status':p.status,'integrity_hash':p.integrity_hash}
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
