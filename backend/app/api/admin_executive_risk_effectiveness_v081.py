from fastapi import APIRouter,Depends,HTTPException,Query
from pydantic import BaseModel,Field
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import ExecutiveRiskEffectiveness
from app.services.executive_risk_effectiveness_v081 import create,measure,verify
router=APIRouter(prefix='/admin/executive-risk-effectiveness',tags=['executive-risk-effectiveness-v081'])
class CreateBody(BaseModel): execution_id:int=Field(gt=0);criteria:str=Field(min_length=3);indicator_code:str='RISK_SCORE'
class MeasureBody(BaseModel): followup_score:float=Field(ge=0,le=100);result:str;notes:str=''
class VerifyBody(BaseModel): note:str=Field(min_length=1)
@router.post('')
def create_route(body:CreateBody,admin=Depends(require_admin),db:Session=Depends(get_db)):
 try:r=create(db,body.execution_id,admin.id,body.criteria,body.indicator_code);db.commit();return {'id':r.id,'status':r.status,'baseline_score':r.baseline_score,'integrity_hash':r.integrity_hash}
 except ValueError as e:db.rollback();raise HTTPException(400,str(e))
@router.get('')
def listing(status:str|None=None,limit:int=Query(100,ge=1,le=500),admin=Depends(require_admin),db:Session=Depends(get_db)):
 q=db.query(ExecutiveRiskEffectiveness).order_by(ExecutiveRiskEffectiveness.created_at.desc());
 if status:q=q.filter(ExecutiveRiskEffectiveness.status==status.upper())
 return {'items':[{'id':r.id,'execution_id':r.execution_id,'status':r.status,'indicator_code':r.indicator_code,'baseline_score':r.baseline_score,'followup_score':r.followup_score,'delta_score':r.delta_score,'effectiveness_result':r.effectiveness_result,'reviewed_by':r.reviewed_by,'integrity_hash':r.integrity_hash} for r in q.limit(limit).all()]}
def getr(db,i):
 r=db.get(ExecutiveRiskEffectiveness,i)
 if not r:raise HTTPException(404,'effectiveness_not_found')
 return r
@router.post('/{id}/measure')
def measure_route(id:int,body:MeasureBody,admin=Depends(require_admin),db:Session=Depends(get_db)):
 try:r=measure(db,getr(db,id),admin.id,body.followup_score,body.result.upper(),body.notes);db.commit();return {'id':r.id,'status':r.status,'delta_score':r.delta_score,'result':r.effectiveness_result,'integrity_hash':r.integrity_hash}
 except ValueError as e:db.rollback();raise HTTPException(400,str(e))
@router.post('/{id}/verify')
def verify_route(id:int,body:VerifyBody,admin=Depends(require_admin),db:Session=Depends(get_db)):
 try:r=verify(db,getr(db,id),admin.id,body.note);db.commit();return {'id':r.id,'status':r.status,'integrity_hash':r.integrity_hash}
 except ValueError as e:db.rollback();raise HTTPException(400,str(e))
