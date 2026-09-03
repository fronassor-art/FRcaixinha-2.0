from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import ContinuousImprovementRecommendation
from app.services.continuous_improvement_v082 import analyze, decide, implement
router=APIRouter(prefix='/admin/continuous-improvement',tags=['continuous-improvement-v082'])
class DecisionBody(BaseModel): decision:str; note:str=Field(min_length=1)
class ImplementBody(BaseModel): note:str=Field(min_length=1)
def out(r): return {'id':r.id,'indicator_code':r.indicator_code,'pattern_code':r.pattern_code,'status':r.status,'sample_size':r.sample_size,'effective_count':r.effective_count,'partial_count':r.partial_count,'ineffective_count':r.ineffective_count,'avg_delta':r.avg_delta,'recommendation':r.recommendation,'decision':r.decision,'decided_by':r.decided_by,'implemented_by':r.implemented_by,'integrity_hash':r.integrity_hash}
@router.post('/analyze')
def analyze_route(admin=Depends(require_admin),db:Session=Depends(get_db)):
 try:r=analyze(db,admin.id);db.commit();return {'created':[out(x) for x in r]}
 except ValueError as e:db.rollback();raise HTTPException(400,str(e))
@router.get('')
def listing(status:str|None=None,limit:int=Query(100,ge=1,le=500),admin=Depends(require_admin),db:Session=Depends(get_db)):
 q=db.query(ContinuousImprovementRecommendation).order_by(ContinuousImprovementRecommendation.created_at.desc())
 if status:q=q.filter(ContinuousImprovementRecommendation.status==status.upper())
 return {'items':[out(x) for x in q.limit(limit).all()]}
def getr(db,i):
 r=db.get(ContinuousImprovementRecommendation,i)
 if not r:raise HTTPException(404,'recommendation_not_found')
 return r
@router.post('/{id}/decide')
def decide_route(id:int,body:DecisionBody,admin=Depends(require_admin),db:Session=Depends(get_db)):
 try:r=decide(db,getr(db,id),admin.id,body.decision.upper(),body.note);db.commit();return out(r)
 except ValueError as e:db.rollback();raise HTTPException(400,str(e))
@router.post('/{id}/implement')
def implement_route(id:int,body:ImplementBody,admin=Depends(require_admin),db:Session=Depends(get_db)):
 try:r=implement(db,getr(db,id),admin.id,body.note);db.commit();return out(r)
 except ValueError as e:db.rollback();raise HTTPException(400,str(e))
