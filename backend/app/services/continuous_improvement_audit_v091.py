from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import (User, ContinuousImprovementRecommendation, ContinuousImprovementPlan,
    ContinuousImprovementAssignmentDecision, ContinuousImprovementExecution,
    ContinuousImprovementExecutionEvidenceFile, ContinuousImprovementEvidenceIntegrityEvent,
    ContinuousImprovementCertification, ContinuousImprovementAuditSnapshot, AuditLog)
from app.services.continuous_improvement_certification_v090 import build_package
from app.services.continuous_improvement_evidence_v089 import verify_execution_evidence, verify_chain

def now(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
def digest(v): return hashlib.sha256(canonical(v).encode()).hexdigest()
def _status(ok): return 'PASS' if ok else 'CRITICAL'

def build_cycle(db: Session, execution_id:int):
    package=build_package(db, execution_id)
    ex=db.get(ContinuousImprovementExecution, execution_id)
    cert=db.query(ContinuousImprovementCertification).filter_by(execution_id=execution_id).first()
    evidence=verify_execution_evidence(db, execution_id, None)
    chain=verify_chain(db)
    checks={
      'execution_verified': ex.status=='VERIFIED',
      'evidence_integrity': bool(evidence.get('valid')),
      'evidence_chain': bool(chain.get('valid')),
      'certified': bool(cert and cert.status=='CERTIFIED'),
      'certificate_hash': bool(cert and cert.package_hash and len(cert.package_hash)==64),
    }
    return {'schema':'v0.91','execution_id':execution_id,'checks':checks,'status':_status(all(checks.values())),
            'package_hash': cert.package_hash if cert else None,'certificate_id': cert.certificate_id if cert else None,
            'certificate': None if not cert else {'id':cert.id,'certificate_id':cert.certificate_id,'status':cert.status,'package_hash':cert.package_hash,'certified_by':cert.certified_by,'certified_at':cert.certified_at,'note':cert.certification_note},
            'cycle':package,'evidence_check':evidence,'chain_check':chain}

def persist(db:Session, execution_id:int, actor_id:int|None=None):
    data=build_cycle(db,execution_id)
    row=ContinuousImprovementAuditSnapshot(execution_id=execution_id,status=data['status'],snapshot_json=canonical(data),snapshot_hash=digest(data),generated_by=actor_id,created_at=now(),updated_at=now())
    db.add(row); db.flush()
    db.add(AuditLog(actor_user_id=actor_id,action='IMPROVEMENT_CYCLE_AUDIT_SNAPSHOT_CREATED',entity_type='ContinuousImprovementAuditSnapshot',entity_id=str(row.id),details=canonical({'execution_id':execution_id,'status':row.status,'snapshot_hash':row.snapshot_hash})))
    return row,data

def verify_snapshot(db:Session, snapshot_id:int):
    row=db.get(ContinuousImprovementAuditSnapshot,snapshot_id)
    if not row: raise ValueError('snapshot_not_found')
    data=json.loads(row.snapshot_json); expected=digest(data); hash_ok=expected==row.snapshot_hash
    current=build_cycle(db,row.execution_id)
    current_hash=digest(current)
    return {'snapshot_id':row.id,'stored_hash':row.snapshot_hash,'hash_valid':hash_ok,'current_cycle_hash':current_hash,'current_cycle_matches':current_hash==row.snapshot_hash,'status':row.status}
