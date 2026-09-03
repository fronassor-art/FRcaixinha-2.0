from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import ExecutiveRiskDecisionExecution, ExecutiveRiskDecisionGovernance, AuditLog

def now(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
def digest(v): return hashlib.sha256(canonical(v).encode()).hexdigest()

def _rehash(row):
    row.execution_hash = digest({'id': row.id, 'governance_id': row.governance_id, 'status': row.status,
        'assigned_to': row.assigned_to, 'started_by': row.started_by, 'started_at': row.started_at,
        'completed_by': row.completed_by, 'completed_at': row.completed_at, 'evidence_note': row.evidence_note,
        'verified_by': row.verified_by, 'verified_at': row.verified_at, 'verification_note': row.verification_note,
        'resolution': row.resolution})

def create_execution(db: Session, governance_id: int, actor_id: int | None, assigned_to: int | None = None):
    gov = db.get(ExecutiveRiskDecisionGovernance, governance_id)
    if not gov: raise ValueError('governance_not_found')
    if gov.validation_status != 'VALIDATED': raise ValueError('governance_must_be_validated')
    existing = db.query(ExecutiveRiskDecisionExecution).filter_by(governance_id=governance_id).first()
    if existing: return existing
    row = ExecutiveRiskDecisionExecution(governance_id=governance_id, status='PENDING', assigned_to=assigned_to,
                                         execution_hash='', created_at=now(), updated_at=now())
    db.add(row); db.flush(); _rehash(row)
    db.add(AuditLog(actor_user_id=actor_id, action='EXECUTIVE_RISK_EXECUTION_CREATED',
                    entity_type='ExecutiveRiskDecisionExecution', entity_id=str(row.id),
                    details=canonical({'governance_id': governance_id, 'assigned_to': assigned_to})))
    return row

def assign(db: Session, row, actor_id: int | None, assigned_to: int):
    if row.status not in {'PENDING','ASSIGNED'}: raise ValueError('invalid_status_for_assignment')
    row.assigned_to = assigned_to; row.status = 'ASSIGNED'; row.updated_at = now(); _rehash(row)
    db.add(AuditLog(actor_user_id=actor_id, action='EXECUTIVE_RISK_EXECUTION_ASSIGNED', entity_type='ExecutiveRiskDecisionExecution', entity_id=str(row.id), details=canonical({'assigned_to': assigned_to})))
    return row

def start(db: Session, row, actor_id: int):
    if row.status not in {'PENDING','ASSIGNED'}: raise ValueError('invalid_status_for_start')
    if row.assigned_to and row.assigned_to != actor_id: raise ValueError('only_assignee_can_start')
    row.started_by=actor_id; row.started_at=now(); row.status='IN_EXECUTION'; row.updated_at=now(); _rehash(row)
    db.add(AuditLog(actor_user_id=actor_id, action='EXECUTIVE_RISK_EXECUTION_STARTED', entity_type='ExecutiveRiskDecisionExecution', entity_id=str(row.id), details=''))
    return row

def complete(db: Session, row, actor_id: int, evidence_note: str, resolution: str):
    if row.status != 'IN_EXECUTION': raise ValueError('execution_not_in_progress')
    if not evidence_note.strip(): raise ValueError('evidence_required')
    if not resolution.strip(): raise ValueError('resolution_required')
    row.completed_by=actor_id; row.completed_at=now(); row.evidence_note=evidence_note.strip(); row.resolution=resolution.strip(); row.status='COMPLETED'; row.updated_at=now(); _rehash(row)
    db.add(AuditLog(actor_user_id=actor_id, action='EXECUTIVE_RISK_EXECUTION_COMPLETED', entity_type='ExecutiveRiskDecisionExecution', entity_id=str(row.id), details=canonical({'evidence': True})))
    return row

def verify(db: Session, row, actor_id: int, note: str):
    if row.status != 'COMPLETED': raise ValueError('execution_must_be_completed')
    if row.completed_by == actor_id: raise ValueError('independent_verifier_required')
    if not note.strip(): raise ValueError('verification_note_required')
    row.verified_by=actor_id; row.verified_at=now(); row.verification_note=note.strip(); row.status='VERIFIED'; row.updated_at=now(); _rehash(row)
    db.add(AuditLog(actor_user_id=actor_id, action='EXECUTIVE_RISK_EXECUTION_VERIFIED', entity_type='ExecutiveRiskDecisionExecution', entity_id=str(row.id), details=canonical({'verified': True})))
    return row
