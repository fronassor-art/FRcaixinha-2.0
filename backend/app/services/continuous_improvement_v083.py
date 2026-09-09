from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from app.models import ContinuousImprovementRecommendation, ContinuousImprovementPlan, ContinuousImprovementMeasurement, AuditLog

def now(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
def digest(v): return hashlib.sha256(canonical(v).encode()).hexdigest()

def _plan_hash(p):
    p.integrity_hash=digest({'id':p.id,'recommendation_id':p.recommendation_id,'status':p.status,'indicator_code':p.indicator_code,'baseline_value':p.baseline_value,'target_value':p.target_value,'target_direction':p.target_direction,'objective':p.objective,'assigned_to':p.assigned_to,'due_at':p.due_at,'implemented_at':p.implemented_at,'implementation_note':p.implementation_note,'created_at':p.created_at})

def _measurement_hash(m):
    m.integrity_hash=digest({'id':m.id,'plan_id':m.plan_id,'measurement_type':m.measurement_type,'value':m.value,'delta':m.delta,'result':m.result,'evidence_note':m.evidence_note,'measured_by':m.measured_by,'measured_at':m.measured_at})

def create_plan(db: Session, recommendation_id:int, actor_id:int|None=None, target_value:float|None=None, target_direction:str='DECREASE', due_days:int=30, objective:str='Melhorar o indicador associado à recomendação aceita.'):
    rec=db.get(ContinuousImprovementRecommendation,recommendation_id)
    if not rec: raise ValueError('recommendation_not_found')
    if rec.status!='ACCEPTED': raise ValueError('recommendation_must_be_accepted')
    existing=db.query(ContinuousImprovementPlan).filter_by(recommendation_id=recommendation_id,status='OPEN').first()
    if existing: return existing
    direction=target_direction.upper()
    if direction not in {'DECREASE','INCREASE','STABLE'}: raise ValueError('invalid_target_direction')
    baseline=rec.avg_delta
    p=ContinuousImprovementPlan(recommendation_id=rec.id,status='OPEN',indicator_code=rec.indicator_code,baseline_value=baseline,target_value=target_value,target_direction=direction,objective=objective.strip(),assigned_to=None,due_at=now()+timedelta(days=max(1,due_days)),created_at=now(),updated_at=now(),integrity_hash='')
    db.add(p); db.flush(); _plan_hash(p)
    db.add(AuditLog(actor_user_id=actor_id,action='IMPROVEMENT_PLAN_CREATED',entity_type='ContinuousImprovementPlan',entity_id=str(p.id),details=canonical({'recommendation_id':rec.id,'indicator_code':p.indicator_code})))
    return p

def assign(db:Session,p,actor_id:int,assigned_to:int):
    if p.status not in {'OPEN','IN_PROGRESS'}: raise ValueError('invalid_plan_status')
    p.assigned_to=assigned_to; p.status='IN_PROGRESS'; p.updated_at=now(); _plan_hash(p)
    db.add(AuditLog(actor_user_id=actor_id,action='IMPROVEMENT_PLAN_ASSIGNED',entity_type='ContinuousImprovementPlan',entity_id=str(p.id),details=canonical({'assigned_to':assigned_to})))
    return p

def implement(db:Session,p,actor_id:int,note:str):
    if p.status!='IN_PROGRESS': raise ValueError('plan_must_be_in_progress')
    if not note.strip(): raise ValueError('implementation_note_required')
    p.status='IMPLEMENTED'; p.implementation_note=note.strip(); p.implemented_at=now(); p.updated_at=now(); _plan_hash(p)
    db.add(AuditLog(actor_user_id=actor_id,action='IMPROVEMENT_PLAN_IMPLEMENTED',entity_type='ContinuousImprovementPlan',entity_id=str(p.id),details=canonical({'implemented':True})))
    return p

def measure(db:Session,p,actor_id:int,value:float,evidence_note:str):
    if p.status!='IMPLEMENTED': raise ValueError('plan_must_be_implemented')
    if not evidence_note.strip(): raise ValueError('evidence_note_required')
    prev=db.query(ContinuousImprovementMeasurement).filter_by(plan_id=p.id).order_by(ContinuousImprovementMeasurement.measured_at.desc()).first()
    baseline=p.baseline_value if prev is None else prev.value
    delta=value-baseline if baseline is not None else None
    result='PENDING'
    if p.target_value is not None:
        ok={'DECREASE':value<=p.target_value,'INCREASE':value>=p.target_value,'STABLE':abs(value-p.target_value)<=0.01}[p.target_direction]
        result='EFFECTIVE' if ok else 'INEFFECTIVE'
    elif delta is not None:
        result='EFFECTIVE' if ((p.target_direction=='DECREASE' and delta<0) or (p.target_direction=='INCREASE' and delta>0) or (p.target_direction=='STABLE' and abs(delta)<=0.01)) else 'INEFFECTIVE'
    m=ContinuousImprovementMeasurement(plan_id=p.id,measurement_type='FOLLOW_UP',value=value,baseline_value=baseline,delta=delta,result=result,evidence_note=evidence_note.strip(),measured_by=actor_id,measured_at=now(),created_at=now(),integrity_hash='')
    db.add(m); db.flush(); _measurement_hash(m); p.status='MEASURED'; p.updated_at=now(); _plan_hash(p)
    db.add(AuditLog(actor_user_id=actor_id,action='IMPROVEMENT_MEASURED',entity_type='ContinuousImprovementPlan',entity_id=str(p.id),details=canonical({'measurement_id':m.id,'result':result})))
    return m

def verify(db:Session,p,actor_id:int,note:str):
    if p.status!='MEASURED': raise ValueError('plan_must_be_measured')
    if not note.strip(): raise ValueError('verification_note_required')
    m=db.query(ContinuousImprovementMeasurement).filter_by(plan_id=p.id).order_by(ContinuousImprovementMeasurement.measured_at.desc()).first()
    if not m: raise ValueError('measurement_not_found')
    if m.measured_by==actor_id: raise ValueError('independent_verification_required')
    m.verification_note=note.strip(); m.verified_by=actor_id; m.verified_at=now(); _measurement_hash(m)
    p.status='CLOSED' if m.result=='EFFECTIVE' else 'REVIEW_REQUIRED'; p.closed_at=now() if p.status=='CLOSED' else None; p.updated_at=now(); _plan_hash(p)
    db.add(AuditLog(actor_user_id=actor_id,action='IMPROVEMENT_EFFECTIVENESS_VERIFIED',entity_type='ContinuousImprovementPlan',entity_id=str(p.id),details=canonical({'measurement_id':m.id,'result':m.result,'status':p.status})))
    return p
