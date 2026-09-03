from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models import (User, ContinuousImprovementRecommendation, ContinuousImprovementPlan,
                        ContinuousImprovementAssignmentSnapshot, ContinuousImprovementAssignmentDecision, ContinuousImprovementAssignmentCapacity, AuditLog)


def now(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
def digest(v): return hashlib.sha256(canonical(v).encode()).hexdigest()


def _eligible(db, uid: int):
    u = db.get(User, uid)
    return bool(u and u.role == 'ADMIN' and u.is_active)


def create_decision(db: Session, snapshot_id: int, recommendation_id: int, decision: str,
                    target_user_id: int | None, actor_id: int, note: str | None = None):
    if decision not in {'ACCEPT', 'REJECT', 'DEFER'}:
        raise ValueError('invalid_decision')
    if not note or not note.strip():
        raise ValueError('decision_note_required')
    snap = db.get(ContinuousImprovementAssignmentSnapshot, snapshot_id)
    if not snap: raise ValueError('snapshot_not_found')
    rec = db.get(ContinuousImprovementRecommendation, recommendation_id)
    if not rec: raise ValueError('recommendation_not_found')
    if not _eligible(db, actor_id): raise ValueError('actor_not_eligible')
    if target_user_id is not None and not _eligible(db, target_user_id): raise ValueError('target_admin_not_eligible')
    if target_user_id == actor_id:
        raise ValueError('self_assignment_conflict')
    payload = {'snapshot_id':snapshot_id,'recommendation_id':recommendation_id,'decision':decision,
               'target_user_id':target_user_id,'actor_id':actor_id,'note':note.strip()}
    h = digest(payload)
    row = ContinuousImprovementAssignmentDecision(snapshot_id=snapshot_id,recommendation_id=recommendation_id,
        target_user_id=target_user_id,decision=decision,decision_note=note.strip(),decided_by=actor_id,
        decided_at=now(),decision_hash=h,created_at=now(),updated_at=now())
    db.add(row); db.flush()
    if decision == 'ACCEPT':
        if target_user_id is None: raise ValueError('target_admin_required')
        plan = db.query(ContinuousImprovementPlan).filter_by(recommendation_id=recommendation_id).with_for_update().first()
        if not plan: raise ValueError('plan_not_found')
        if plan.status == 'CLOSED': raise ValueError('plan_closed')
        # Re-check active load at decision time; never blindly trust the snapshot.
        cap = db.query(ContinuousImprovementAssignmentCapacity).filter_by(user_id=target_user_id).first()
        max_active = cap.max_active_items if cap else 5
        active = db.query(func.count(ContinuousImprovementPlan.id)).filter(
            ContinuousImprovementPlan.assigned_to == target_user_id,
            ContinuousImprovementPlan.status != 'CLOSED',
            ContinuousImprovementPlan.id != plan.id).scalar() or 0
        if active >= max_active:
            raise ValueError('target_capacity_exhausted')
        if rec.decided_by == target_user_id or rec.implemented_by == target_user_id:
            raise ValueError('assignment_conflict')
        plan.assigned_to = target_user_id
        plan.updated_at = now()
        db.add(AuditLog(actor_user_id=actor_id, action='IMPROVEMENT_ASSIGNMENT_DECISION_ACCEPTED', entity_type='ContinuousImprovementAssignmentDecision', entity_id=str(row.id), details=canonical({'recommendation_id': recommendation_id, 'plan_id': plan.id, 'assigned_to': target_user_id})))
    else:
        db.add(AuditLog(actor_user_id=actor_id, action=f'IMPROVEMENT_ASSIGNMENT_DECISION_{decision}', entity_type='ContinuousImprovementAssignmentDecision', entity_id=str(row.id), details=canonical({'recommendation_id': recommendation_id, 'target_user_id': target_user_id})))
    return row
