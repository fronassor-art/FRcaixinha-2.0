from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import ExecutiveRiskDecision, ExecutiveRiskDecisionGovernance, AuditLog

def now(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
def _hash(v): return hashlib.sha256(canonical(v).encode()).hexdigest()

def required_approvals(priority: str) -> int:
    return 2 if priority.upper() in {'HIGH','CRITICAL'} else 1

def build_governance(db: Session, decision: ExecutiveRiskDecision):
    existing = db.query(ExecutiveRiskDecisionGovernance).filter_by(decision_id=decision.id).first()
    if existing: return existing
    required = required_approvals(decision.priority)
    row = ExecutiveRiskDecisionGovernance(
        decision_id=decision.id, required_approvals=required, approvals_count=0,
        status='PENDING', conflict_status='NOT_CHECKED', validation_status='PENDING',
        conditions_required=required >= 2, integrity_hash='', created_at=now(), updated_at=now()
    )
    db.add(row); db.flush()
    row.integrity_hash = _hash({'decision_id': decision.id, 'required_approvals': required,
                                'conditions_required': row.conditions_required, 'status': row.status})
    db.add(AuditLog(actor_user_id=decision.requested_by, action='EXECUTIVE_RISK_GOVERNANCE_CREATED',
                    entity_type='ExecutiveRiskDecisionGovernance', entity_id=str(row.id),
                    details=canonical({'decision_id': decision.id, 'required_approvals': required})))
    return row

def approve(db: Session, governance: ExecutiveRiskDecisionGovernance, actor_id: int, conditions: str | None = None):
    decision = db.get(ExecutiveRiskDecision, governance.decision_id)
    if not decision: raise ValueError('decision_not_found')
    if governance.status == 'AUTHORIZED': raise ValueError('already_authorized')
    if decision.decision != 'APPROVE': raise ValueError('only_approved_decision_can_be_authorized')
    if decision.requested_by == actor_id: raise ValueError('conflict_of_interest')
    if governance.conditions_required and not ((conditions or decision.conditions or '').strip()):
        raise ValueError('conditions_required')
    if actor_id in {governance.primary_approver_id, governance.secondary_approver_id}:
        raise ValueError('approver_already_recorded')
    if governance.approvals_count == 0:
        governance.primary_approver_id = actor_id
        governance.approvals_count = 1
        governance.conflict_status = 'PASS'
    elif governance.approvals_count == 1:
        if actor_id == governance.primary_approver_id: raise ValueError('distinct_approver_required')
        governance.secondary_approver_id = actor_id
        governance.approvals_count = 2
    else:
        raise ValueError('approval_limit_reached')
    if conditions and conditions.strip(): decision.conditions = conditions.strip()
    governance.status = 'AUTHORIZED' if governance.approvals_count >= governance.required_approvals else 'AWAITING_SECOND_APPROVAL'
    governance.updated_at = now()
    governance.integrity_hash = _hash({'id': governance.id, 'decision_id': decision.id,
        'required_approvals': governance.required_approvals, 'approvals_count': governance.approvals_count,
        'primary_approver_id': governance.primary_approver_id, 'secondary_approver_id': governance.secondary_approver_id,
        'status': governance.status, 'conditions': decision.conditions})
    db.add(AuditLog(actor_user_id=actor_id, action='EXECUTIVE_RISK_GOVERNANCE_APPROVAL',
                    entity_type='ExecutiveRiskDecisionGovernance', entity_id=str(governance.id),
                    details=canonical({'decision_id': decision.id, 'status': governance.status, 'approvals_count': governance.approvals_count})))
    return governance

def validate(db: Session, governance: ExecutiveRiskDecisionGovernance, actor_id: int):
    decision = db.get(ExecutiveRiskDecision, governance.decision_id)
    if not decision: raise ValueError('decision_not_found')
    if governance.status != 'AUTHORIZED': raise ValueError('decision_not_fully_authorized')
    if governance.primary_approver_id == governance.secondary_approver_id and governance.required_approvals > 1:
        raise ValueError('distinct_approver_required')
    if governance.conditions_required and not (decision.conditions or '').strip():
        raise ValueError('conditions_required')
    governance.validation_status = 'VALIDATED'
    governance.validated_by = actor_id
    governance.validated_at = now()
    governance.updated_at = now()
    governance.integrity_hash = _hash({'id': governance.id, 'decision_id': decision.id, 'status': governance.status,
        'validation_status': governance.validation_status, 'validated_by': actor_id,
        'approvals': [governance.primary_approver_id, governance.secondary_approver_id], 'conditions': decision.conditions})
    db.add(AuditLog(actor_user_id=actor_id, action='EXECUTIVE_RISK_GOVERNANCE_VALIDATED',
                    entity_type='ExecutiveRiskDecisionGovernance', entity_id=str(governance.id),
                    details=canonical({'decision_id': decision.id, 'validation_status': governance.validation_status})))
    return governance
