from __future__ import annotations
import hashlib, json
from sqlalchemy.orm import Session
from app.models import AllocationDecisionRecord, AllocationTransparencySnapshot, AuditLog

def canonical_json(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)

def decision_hash(*, snapshot_id, transparency_hash, policy_version, decision, exception_applied, exception_reason, admin_note):
    payload = {"snapshot_id": snapshot_id, "transparency_hash": transparency_hash, "policy_version": policy_version,
               "decision": decision, "exception_applied": exception_applied, "exception_reason": exception_reason, "admin_note": admin_note}
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()

def create_decision(db: Session, *, snapshot, actor_id: int, decision: str, requested_by: int|None=None,
                    exception_applied=False, exception_reason=None, admin_note=None):
    decision = decision.upper()
    if decision not in {"APPROVED", "REJECTED", "DEFERRED"}: raise ValueError("Decisão inválida.")
    if exception_applied and not (exception_reason and str(exception_reason).strip()):
        raise ValueError("Exceção exige justificativa.")
    h = decision_hash(snapshot_id=snapshot.id, transparency_hash=snapshot.explanation_hash,
                      policy_version=snapshot.policy_version, decision=decision,
                      exception_applied=exception_applied, exception_reason=exception_reason, admin_note=admin_note)
    row = AllocationDecisionRecord(transparency_snapshot_id=snapshot.id, group_id=snapshot.group_id,
        requested_by=requested_by, analyzed_by=actor_id, decided_by=actor_id, decision=decision,
        policy_version=snapshot.policy_version, transparency_hash=snapshot.explanation_hash,
        decision_input_hash=h, exception_applied=exception_applied,
        exception_reason=exception_reason, admin_note=admin_note)
    db.add(row)
    db.add(AuditLog(actor_user_id=actor_id, action="ALLOCATION_DECISION", entity_type="ALLOCATION_DECISION",
                    entity_id="pending", details=canonical_json({"decision":decision,"snapshot_id":snapshot.id,"hash":h,"exception":exception_applied})))
    return row, h
