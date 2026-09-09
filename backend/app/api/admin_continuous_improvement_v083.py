from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import ContinuousImprovementPlan, ContinuousImprovementMeasurement
from app.services.continuous_improvement_v083 import create_plan, assign, implement, measure, verify
router=APIRouter(prefix='/admin/continuous-improvement-v083',tags=['continuous-improvement-v083'])
class PlanBody(BaseModel): target_value:float|None=None; target_direction:str='DECREASE'; due_days:int=Field(30,ge=1,le=365); objective:str=Field(min_length=1)
class AssignBody(BaseModel): assigned_to:int
class NoteBody(BaseModel): note:str=Field(min_length=1)
class MeasureBody(BaseModel): value:float; evidence_note:str=Field(min_length=1)

def out(p):
    return {'id':p.id,'recommendation_id':p.recommendation_id,'status':p.status,'indicator_code':p.indicator_code,'baseline_value':p.baseline_value,'target_value':p.target_value,'target_direction':p.target_direction,'objective':p.objective,'assigned_to':p.assigned_to,'due_at':p.due_at,'implemented_at':p.implemented_at,'closed_at':p.closed_at,'integrity_hash':p.integrity_hash}
def mout(m): return {'id':m.id,'plan_id':m.plan_id,'value':m.value,'baseline_value':m.baseline_value,'delta':m.delta,'result':m.result,'evidence_note':m.evidence_note,'measured_by':m.measured_by,'measured_at':m.measured_at,'verified_by':m.verified_by,'verified_at':m.verified_at,'verification_note':m.verification_note,'integrity_hash':m.integrity_hash}
def getp(db,i):
    p=db.get(ContinuousImprovementPlan,i)
    if not p: raise HTTPException(404,'plan_not_found')
    return p
@router.post('/plans')
def plan_route(body:PlanBody, recommendation_id:int=Query(...), admin=Depends(require_admin), db:Session=Depends(get_db)):
    try:p=create_plan(db,recommendation_id,admin.id,body.target_value,body.target_direction,body.due_days,body.objective);db.commit();return out(p)
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
@router.get('/plans')
def plans(status:str|None=None,limit:int=Query(100,ge=1,le=500),admin=Depends(require_admin),db:Session=Depends(get_db)):
    q=db.query(ContinuousImprovementPlan).order_by(ContinuousImprovementPlan.created_at.desc())
    if status:q=q.filter(ContinuousImprovementPlan.status==status.upper())
    return {'items':[out(x) for x in q.limit(limit).all()]}
@router.post('/plans/{id}/assign')
def assign_route(id:int,body:AssignBody,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try:p=assign(db,getp(db,id),admin.id,body.assigned_to);db.commit();return out(p)
    except ValueError as e:db.rollback();raise HTTPException(400,str(e))
@router.post('/plans/{id}/implement')
def implement_route(id:int,body:NoteBody,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try:p=implement(db,getp(db,id),admin.id,body.note);db.commit();return out(p)
    except ValueError as e:db.rollback();raise HTTPException(400,str(e))
@router.post('/plans/{id}/measure')
def measure_route(id:int,body:MeasureBody,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try:m=measure(db,getp(db,id),admin.id,body.value,body.evidence_note);db.commit();return mout(m)
    except ValueError as e:db.rollback();raise HTTPException(400,str(e))
@router.post('/plans/{id}/verify')
def verify_route(id:int,body:NoteBody,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try:p=verify(db,getp(db,id),admin.id,body.note);db.commit();return out(p)
    except ValueError as e:db.rollback();raise HTTPException(400,str(e))
@router.get('/plans/{id}/measurements')
def measurements(id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    getp(db,id); return {'items':[mout(x) for x in db.query(ContinuousImprovementMeasurement).filter_by(plan_id=id).order_by(ContinuousImprovementMeasurement.measured_at.desc()).all()]}
