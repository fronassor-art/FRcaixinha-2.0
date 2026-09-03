from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import ExecutiveRiskDecisionExecution, ExecutiveRiskEffectiveness, OperationalRiskTrendSnapshot, AuditLog

def now(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
def digest(v): return hashlib.sha256(canonical(v).encode()).hexdigest()

def _rehash(r):
    r.integrity_hash = digest({'id':r.id,'execution_id':r.execution_id,'status':r.status,'baseline_score':r.baseline_score,'followup_score':r.followup_score,'delta_score':r.delta_score,'indicator_code':r.indicator_code,'effectiveness_criteria':r.effectiveness_criteria,'effectiveness_result':r.effectiveness_result,'reviewed_by':r.reviewed_by,'reviewed_at':r.reviewed_at,'notes':r.notes})

def create(db:Session, execution_id:int, actor_id:int|None, criteria:str, indicator_code:str='RISK_SCORE'):
    ex=db.get(ExecutiveRiskDecisionExecution, execution_id)
    if not ex: raise ValueError('execution_not_found')
    if ex.status!='VERIFIED': raise ValueError('execution_must_be_verified')
    existing=db.query(ExecutiveRiskEffectiveness).filter_by(execution_id=execution_id).first()
    if existing:return existing
    baseline=db.query(OperationalRiskTrendSnapshot).filter(OperationalRiskTrendSnapshot.snapshot_date <= ex.created_at.date()).order_by(OperationalRiskTrendSnapshot.snapshot_date.desc()).first()
    r=ExecutiveRiskEffectiveness(execution_id=execution_id,status='PENDING',indicator_code=indicator_code,effectiveness_criteria=criteria.strip(),baseline_score=(baseline.risk_score if baseline else None),created_at=now(),updated_at=now(),integrity_hash='')
    db.add(r);db.flush();_rehash(r)
    db.add(AuditLog(actor_user_id=actor_id,action='EXECUTIVE_RISK_EFFECTIVENESS_CREATED',entity_type='ExecutiveRiskEffectiveness',entity_id=str(r.id),details=canonical({'execution_id':execution_id,'indicator_code':indicator_code})))
    return r

def measure(db:Session,r,actor_id:int,followup_score:float,result:str,notes:str):
    if r.status not in {'PENDING','MEASURED'}:raise ValueError('invalid_status_for_measurement')
    if not 0<=followup_score<=100:raise ValueError('followup_score_out_of_range')
    if result not in {'EFFECTIVE','PARTIAL','INEFFECTIVE'}:raise ValueError('invalid_effectiveness_result')
    r.followup_score=followup_score;r.delta_score=(followup_score-r.baseline_score) if r.baseline_score is not None else None;r.effectiveness_result=result;r.notes=notes.strip();r.status='MEASURED';r.updated_at=now();_rehash(r)
    db.add(AuditLog(actor_user_id=actor_id,action='EXECUTIVE_RISK_EFFECTIVENESS_MEASURED',entity_type='ExecutiveRiskEffectiveness',entity_id=str(r.id),details=canonical({'result':result,'followup_score':followup_score})))
    return r

def verify(db:Session,r,actor_id:int,note:str):
    if r.status!='MEASURED':raise ValueError('effectiveness_must_be_measured')
    if not note.strip():raise ValueError('verification_note_required')
    if r.reviewed_by==actor_id:raise ValueError('independent_reviewer_required')
    r.reviewed_by=actor_id;r.reviewed_at=now();r.notes=(r.notes or '')+' | Review: '+note.strip();r.status='VERIFIED';r.updated_at=now();_rehash(r)
    db.add(AuditLog(actor_user_id=actor_id,action='EXECUTIVE_RISK_EFFECTIVENESS_VERIFIED',entity_type='ExecutiveRiskEffectiveness',entity_id=str(r.id),details=canonical({'result':r.effectiveness_result})))
    return r
