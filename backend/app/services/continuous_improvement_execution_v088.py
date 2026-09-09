from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import (ContinuousImprovementAssignmentDecision, ContinuousImprovementPlan,
                        ContinuousImprovementExecution, User, AuditLog)

def now(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
def digest(v): return hashlib.sha256(canonical(v).encode()).hexdigest()

def _eligible(db, uid):
    u=db.get(User, uid)
    return bool(u and u.role=='ADMIN' and u.is_active)

def _hash_payload(row):
    return {'decision_id':row.decision_id,'recommendation_id':row.recommendation_id,'plan_id':row.plan_id,
            'assigned_to':row.assigned_to,'status':row.status,'started_at':row.started_at,'completed_at':row.completed_at,
            'verified_by':row.verified_by,'verified_at':row.verified_at,'resolution_note':row.resolution_note,'evidence_note':row.evidence_note}

def _refresh_hash(row):
    row.execution_hash=digest(_hash_payload(row)); row.updated_at=now()

def create_from_decision(db: Session, decision_id: int, actor_id: int|None=None):
    d=db.get(ContinuousImprovementAssignmentDecision, decision_id)
    if not d: raise ValueError('decision_not_found')
    if d.decision!='ACCEPT': raise ValueError('decision_not_accepted')
    existing=db.query(ContinuousImprovementExecution).filter_by(decision_id=decision_id).first()
    if existing: return existing
    plan=db.query(ContinuousImprovementPlan).filter_by(recommendation_id=d.recommendation_id).first()
    if not plan: raise ValueError('plan_not_found')
    if plan.status=='CLOSED': raise ValueError('plan_closed')
    if not plan.assigned_to: raise ValueError('plan_not_assigned')
    row=ContinuousImprovementExecution(decision_id=d.id,recommendation_id=d.recommendation_id,plan_id=plan.id,
        status='PENDING',assigned_to=plan.assigned_to,created_at=now(),updated_at=now())
    db.add(row); db.flush(); _refresh_hash(row)
    db.add(AuditLog(actor_user_id=actor_id,action='IMPROVEMENT_EXECUTION_CREATED',entity_type='ContinuousImprovementExecution',entity_id=str(row.id),details=canonical({'decision_id':d.id,'plan_id':plan.id,'assigned_to':plan.assigned_to})))
    return row

def accept_execution(db, execution_id, actor_id):
    row=db.get(ContinuousImprovementExecution,execution_id)
    if not row: raise ValueError('execution_not_found')
    if not _eligible(db,actor_id): raise ValueError('actor_not_eligible')
    if actor_id!=row.assigned_to: raise ValueError('only_assignee_can_start')
    if row.status!='PENDING': raise ValueError('invalid_state')
    row.status='IN_EXECUTION'; row.started_at=now(); _refresh_hash(row)
    db.add(AuditLog(actor_user_id=actor_id,action='IMPROVEMENT_EXECUTION_STARTED',entity_type='ContinuousImprovementExecution',entity_id=str(row.id),details=canonical({'execution_hash':row.execution_hash})))
    return row

def complete_execution(db, execution_id, actor_id, resolution_note, evidence_note):
    row=db.get(ContinuousImprovementExecution,execution_id)
    if not row: raise ValueError('execution_not_found')
    if not _eligible(db,actor_id): raise ValueError('actor_not_eligible')
    if actor_id!=row.assigned_to: raise ValueError('only_assignee_can_complete')
    if row.status!='IN_EXECUTION': raise ValueError('invalid_state')
    if not resolution_note or len(resolution_note.strip())<3: raise ValueError('resolution_note_required')
    if not evidence_note or len(evidence_note.strip())<3: raise ValueError('evidence_note_required')
    row.status='COMPLETED'; row.completed_at=now(); row.resolution_note=resolution_note.strip(); row.evidence_note=evidence_note.strip(); _refresh_hash(row)
    db.add(AuditLog(actor_user_id=actor_id,action='IMPROVEMENT_EXECUTION_COMPLETED',entity_type='ContinuousImprovementExecution',entity_id=str(row.id),details=canonical({'execution_hash':row.execution_hash})))
    return row

def verify_execution(db, execution_id, actor_id, note):
    row=db.get(ContinuousImprovementExecution,execution_id)
    if not row: raise ValueError('execution_not_found')
    if not _eligible(db,actor_id): raise ValueError('actor_not_eligible')
    if actor_id==row.assigned_to: raise ValueError('independent_verifier_required')
    if row.status!='COMPLETED': raise ValueError('invalid_state')
    if not note or len(note.strip())<3: raise ValueError('verification_note_required')
    from app.services.continuous_improvement_evidence_v089 import verify_execution_evidence
    evidence_check=verify_execution_evidence(db, execution_id, actor_id)
    if not evidence_check['valid']: raise ValueError('evidence_integrity_failed')
    row.status='VERIFIED'; row.verified_by=actor_id; row.verified_at=now(); row.verification_note=note.strip(); _refresh_hash(row)
    plan=db.get(ContinuousImprovementPlan,row.plan_id)
    if plan and plan.status!='CLOSED':
        plan.status='IMPLEMENTED'; plan.implemented_at=plan.implemented_at or now(); plan.implementation_note=row.resolution_note; plan.updated_at=now()
    db.add(AuditLog(actor_user_id=actor_id,action='IMPROVEMENT_EXECUTION_VERIFIED',entity_type='ContinuousImprovementExecution',entity_id=str(row.id),details=canonical({'execution_hash':row.execution_hash,'independent_verifier':actor_id})))
    return row
