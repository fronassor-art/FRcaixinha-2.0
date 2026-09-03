from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import (ContinuousImprovementRecommendation, ContinuousImprovementPlan,
    ContinuousImprovementAssignmentDecision, ContinuousImprovementExecution,
    ContinuousImprovementExecutionEvidenceFile, ContinuousImprovementEvidenceIntegrityEvent,
    ContinuousImprovementCertification, ContinuousImprovementAuditSnapshot,
    ContinuousImprovementExecutiveAuditSnapshot, AuditLog)
from app.services.continuous_improvement_audit_v091 import build_cycle, digest
from app.services.continuous_improvement_evidence_v089 import verify_execution_evidence, verify_chain

def now(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)

def build_report(db: Session):
    recs = db.query(ContinuousImprovementRecommendation).all()
    plans = db.query(ContinuousImprovementPlan).all()
    decisions = db.query(ContinuousImprovementAssignmentDecision).all()
    executions = db.query(ContinuousImprovementExecution).all()
    certs = db.query(ContinuousImprovementCertification).all()
    audits = db.query(ContinuousImprovementAuditSnapshot).all()
    status_counts = {}
    for r in recs: status_counts[r.status] = status_counts.get(r.status, 0) + 1
    plan_counts = {}
    for p in plans: plan_counts[p.status] = plan_counts.get(p.status, 0) + 1
    exec_counts = {}
    for e in executions: exec_counts[e.status] = exec_counts.get(e.status, 0) + 1
    decision_counts = {}
    for d in decisions: decision_counts[d.decision] = decision_counts.get(d.decision, 0) + 1
    integrity_failures = 0
    certified = 0
    cycle_items = []
    for e in executions:
        cert = next((c for c in certs if c.execution_id == e.id), None)
        audit = next((a for a in audits if a.execution_id == e.id), None)
        evidence = verify_execution_evidence(db, e.id, None)
        checks = {
            'execution_verified': e.status == 'VERIFIED',
            'evidence_integrity': bool(evidence.get('valid')),
            'certified': bool(cert and cert.status == 'CERTIFIED'),
            'audit_snapshot': bool(audit),
        }
        if not all(checks.values()): integrity_failures += 1
        if checks['certified']: certified += 1
        cycle_items.append({'execution_id': e.id, 'status': e.status, 'checks': checks,
            'certificate_id': cert.certificate_id if cert else None,
            'audit_snapshot_id': audit.id if audit else None})
    total_exec = len(executions)
    verified = sum(1 for e in executions if e.status == 'VERIFIED')
    open_plans = sum(1 for p in plans if p.status not in ('CLOSED','MEASURED'))
    overdue_plans = sum(1 for p in plans if p.due_at and p.due_at < now() and p.status not in ('CLOSED','MEASURED'))
    overall = 'CRITICAL' if integrity_failures else ('ATTENTION' if overdue_plans or (verified and certified < verified) else 'PASS')
    return {'schema':'v0.92','generated_at':now().isoformat(),'status':overall,
        'indicators': {'recommendations':len(recs),'plans':len(plans),'open_plans':open_plans,'overdue_plans':overdue_plans,
                       'executions':total_exec,'verified_executions':verified,'certified_executions':certified,
                       'integrity_failures':integrity_failures},
        'counts': {'recommendations':status_counts,'plans':plan_counts,'decisions':decision_counts,'executions':exec_counts},
        'cycles':cycle_items}

def persist_report(db: Session, actor_id: int|None=None):
    data=build_report(db)
    # Hash excludes generated_at so identical logical state produces identical integrity hash.
    hash_payload=dict(data); hash_payload.pop('generated_at',None)
    h=digest(hash_payload)
    row=ContinuousImprovementExecutiveAuditSnapshot(status=data['status'], snapshot_json=canonical(data), snapshot_hash=h, generated_by=actor_id, created_at=now(), updated_at=now())
    db.add(row); db.flush()
    db.add(AuditLog(actor_user_id=actor_id, action='IMPROVEMENT_EXECUTIVE_AUDIT_REPORT_CREATED', entity_type='ContinuousImprovementExecutiveAuditSnapshot', entity_id=str(row.id), details=canonical({'status':row.status,'snapshot_hash':h})))
    return row,data

def verify_report(db: Session, snapshot_id:int):
    row=db.get(ContinuousImprovementExecutiveAuditSnapshot,snapshot_id)
    if not row: raise ValueError('snapshot_not_found')
    stored=json.loads(row.snapshot_json); payload=dict(stored); payload.pop('generated_at',None)
    stored_hash=digest(payload); current=build_report(db); current_payload=dict(current); current_payload.pop('generated_at',None)
    return {'snapshot_id':row.id,'hash_valid':stored_hash==row.snapshot_hash,'stored_hash':row.snapshot_hash,
            'current_hash':digest(current_payload),'current_matches':digest(current_payload)==row.snapshot_hash,'status':row.status}
