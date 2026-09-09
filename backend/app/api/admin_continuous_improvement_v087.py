from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import ContinuousImprovementAssignmentDecision, ContinuousImprovementAssignmentSnapshot
from app.services.continuous_improvement_governance_v087 import create_decision
import json
router=APIRouter(prefix='/admin/continuous-improvement-governance',tags=['continuous-improvement-v087'])
class DecisionIn(BaseModel):
    snapshot_id:int=Field(gt=0)
    recommendation_id:int=Field(gt=0)
    decision:str
    target_user_id:int|None=None
    note:str=Field(min_length=3,max_length=2000)
@router.post('/decisions')
def decide(body:DecisionIn,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: row=create_decision(db,body.snapshot_id,body.recommendation_id,body.decision,body.target_user_id,admin.id,body.note); db.commit()
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
    return {'id':row.id,'decision':row.decision,'decision_hash':row.decision_hash,'target_user_id':row.target_user_id,'status':'RECORDED'}
@router.get('/decisions')
def decisions(limit:int=Query(50,ge=1,le=200),admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=db.query(ContinuousImprovementAssignmentDecision).order_by(ContinuousImprovementAssignmentDecision.id.desc()).limit(limit).all()
    return {'items':[{'id':r.id,'snapshot_id':r.snapshot_id,'recommendation_id':r.recommendation_id,'decision':r.decision,'target_user_id':r.target_user_id,'decided_by':r.decided_by,'decided_at':r.decided_at.isoformat(),'decision_hash':r.decision_hash} for r in rows]}
@router.get('/decisions/{id}')
def detail(id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    r=db.get(ContinuousImprovementAssignmentDecision,id)
    if not r: raise HTTPException(404,'decision_not_found')
    return {'id':r.id,'snapshot_id':r.snapshot_id,'recommendation_id':r.recommendation_id,'decision':r.decision,'target_user_id':r.target_user_id,'decided_by':r.decided_by,'decided_at':r.decided_at.isoformat(),'decision_note':r.decision_note,'decision_hash':r.decision_hash}
