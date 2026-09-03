from fastapi import APIRouter,Depends,HTTPException,Query
from pydantic import BaseModel,Field
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import ExecutiveRiskDecision
from app.services.executive_risk_decision_v078 import create_decision,decide
router=APIRouter(prefix='/admin/executive-risk-decisions',tags=['executive-risk-decisions-v078'])
class DecisionCreate(BaseModel):
    snapshot_id:int|None=None; alert_id:int|None=None; response_plan_id:int|None=None; priority:str='MEDIUM'; decision_type:str='OPERATIONAL_REVIEW'; recommendation:str=''
class DecisionMake(BaseModel):
    decision:str=Field(min_length=1); rationale:str=Field(min_length=1); conditions:str|None=None
@router.post('')
def create(body:DecisionCreate,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: row=create_decision(db,requested_by=admin.id,**body.model_dump()); db.commit(); return {'id':row.id,'status':row.status,'decision_hash':row.decision_hash}
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
@router.get('')
def listing(status:str|None=None,limit:int=Query(50,ge=1,le=200),admin=Depends(require_admin),db:Session=Depends(get_db)):
    q=db.query(ExecutiveRiskDecision).order_by(ExecutiveRiskDecision.created_at.desc())
    if status:q=q.filter(ExecutiveRiskDecision.status==status.upper())
    rows=q.limit(limit).all(); return {'items':[{'id':r.id,'status':r.status,'priority':r.priority,'decision_type':r.decision_type,'decision':r.decision,'snapshot_id':r.snapshot_id,'alert_id':r.alert_id,'response_plan_id':r.response_plan_id,'requested_by':r.requested_by,'decided_by':r.decided_by,'decided_at':r.decided_at,'decision_hash':r.decision_hash} for r in rows]}
@router.post('/{decision_id}/decide')
def make(decision_id:int,body:DecisionMake,admin=Depends(require_admin),db:Session=Depends(get_db)):
    row=db.get(ExecutiveRiskDecision,decision_id)
    if not row: raise HTTPException(404,'decision_not_found')
    try: row=decide(db,row,body.decision,admin.id,body.rationale,body.conditions); db.commit(); return {'id':row.id,'status':row.status,'decision':row.decision,'decision_hash':row.decision_hash}
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
