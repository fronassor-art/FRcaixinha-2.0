from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.models import (
    OperationalWorkflowTask,
    OperationalWorkflowOrchestration,
    WorkflowExecutionEvidenceFile,
    WorkflowEvidenceIntegrityEvent,
    WorkflowExecutionChecklistItem,
    WorkflowExecutionEvidence,
    WorkflowComplianceSnapshot,
)
from app.services.workflow_evidence_integrity_v069 import verify_chain


def utcnow():
    return datetime.now(timezone.utc)


def _canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _check(name, status, count, details):
    return {"name": name, "status": status, "count": count, "details": details}


def build_compliance(db: Session, now=None):
    now = now or utcnow()
    tasks = db.query(OperationalWorkflowTask).all()
    orchs = {o.task_id: o for o in db.query(OperationalWorkflowOrchestration).all()}
    files = db.query(WorkflowExecutionEvidenceFile).all()
    integrity_events = db.query(WorkflowEvidenceIntegrityEvent).all()

    integrity = verify_chain(db)
    latest_by_file = {}
    for ev in sorted(integrity_events, key=lambda x: x.id):
        latest_by_file[ev.file_id] = ev.status

    mismatches = sum(1 for f in files if latest_by_file.get(f.id) == "MISMATCH")
    missing = sum(1 for f in files if latest_by_file.get(f.id) == "MISSING")
    revoked = sum(1 for f in files if f.revoked_at is not None)
    never_verified = sum(1 for f in files if f.id not in latest_by_file)

    overdue = [t for t in tasks if t.status != "COMPLETED" and t.due_at and t.due_at < now]
    critical_overdue = [t for t in overdue if (t.escalation_level or "NONE") == "CRITICAL"]
    execution_anomalies = []
    pending_acceptance = 0
    for task in tasks:
        o = orchs.get(task.id)
        if not o:
            if task.status != "COMPLETED":
                execution_anomalies.append(task.id)
            continue
        if task.status == "COMPLETED" and o.execution_state != "COMPLETED":
            execution_anomalies.append(task.id)
        if task.status == "ASSIGNED" and o.execution_state == "PENDING_ACCEPTANCE":
            pending_acceptance += 1

    completed_missing_requirements = 0
    for task in tasks:
        if task.status != "COMPLETED":
            continue
        required_pending = db.query(WorkflowExecutionChecklistItem).filter(
            WorkflowExecutionChecklistItem.task_id == task.id,
            WorkflowExecutionChecklistItem.required.is_(True),
            WorkflowExecutionChecklistItem.completed.is_(False),
        ).count()
        if required_pending:
            completed_missing_requirements += 1

    checks = [
        _check("EVIDENCE_INTEGRITY_CHAIN", "PASS" if integrity["valid"] else "CRITICAL", len(integrity_events),
               {"events": integrity["events"], "failures": integrity["failures"], "head_hash": integrity["head_hash"]}),
        _check("EVIDENCE_FILE_HASHES", "CRITICAL" if (mismatches or missing) else ("ATTENTION" if never_verified else "PASS"),
               mismatches + missing + never_verified,
               {"mismatch": mismatches, "missing": missing, "never_verified": never_verified, "revoked": revoked}),
        _check("WORKFLOW_SLA", "CRITICAL" if critical_overdue else ("ATTENTION" if overdue else "PASS"), len(overdue),
               {"overdue": len(overdue), "critical_overdue": len(critical_overdue)}),
        _check("EXECUTION_STATE", "CRITICAL" if execution_anomalies else ("ATTENTION" if pending_acceptance else "PASS"),
               len(execution_anomalies) + pending_acceptance,
               {"anomalies": execution_anomalies, "pending_acceptance": pending_acceptance}),
        _check("CHECKLIST_COMPLETION", "CRITICAL" if completed_missing_requirements else "PASS",
               completed_missing_requirements, {"completed_with_required_pending": completed_missing_requirements}),
    ]

    statuses = {c["status"] for c in checks}
    status = "CRITICAL" if "CRITICAL" in statuses else ("ATTENTION" if "ATTENTION" in statuses else "PASS")
    return {
        "status": status,
        "generated_at": now.isoformat(),
        "checks": checks,
        "totals": {"tasks": len(tasks), "files": len(files), "integrity_events": len(integrity_events),
                   "overdue_tasks": len(overdue), "critical_overdue_tasks": len(critical_overdue)},
    }


def persist_compliance_snapshot(db: Session, generated_by: int | None = None, snapshot_date: date | None = None, now=None):
    now = now or utcnow()
    snapshot_date = snapshot_date or now.date()
    result = build_compliance(db, now)
    snapshot_json = _canonical(result)
    snapshot_hash = hashlib.sha256(snapshot_json.encode()).hexdigest()
    row = db.query(WorkflowComplianceSnapshot).filter_by(snapshot_date=snapshot_date).first()
    if row:
        row.status = result["status"]
        row.snapshot_json = snapshot_json
        row.snapshot_hash = snapshot_hash
        row.generated_by = generated_by
        row.created_at = now
    else:
        row = WorkflowComplianceSnapshot(snapshot_date=snapshot_date, status=result["status"],
                                         snapshot_json=snapshot_json, snapshot_hash=snapshot_hash,
                                         generated_by=generated_by, created_at=now)
        db.add(row)
    db.flush()
    return row, result


def latest_compliance(db: Session):
    row = db.query(WorkflowComplianceSnapshot).order_by(WorkflowComplianceSnapshot.snapshot_date.desc()).first()
    return row
