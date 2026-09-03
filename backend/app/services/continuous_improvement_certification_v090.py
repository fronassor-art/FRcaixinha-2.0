from __future__ import annotations
import hashlib, json, uuid
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import (User, ContinuousImprovementRecommendation, ContinuousImprovementPlan,
    ContinuousImprovementAssignmentDecision, ContinuousImprovementExecution,
    ContinuousImprovementExecutionEvidenceFile, ContinuousImprovementEvidenceIntegrityEvent,
    ContinuousImprovementCertification, AuditLog)
from app.services.continuous_improvement_evidence_v089 import verify_execution_evidence, verify_chain

def now(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
def digest(v): return hashlib.sha256(canonical(v).encode()).hexdigest()
def _admin(db, uid):
    u=db.get(User,uid); return bool(u and u.role=='ADMIN' and u.is_active)

def build_package(db: Session, execution_id: int):
    ex=db.get(ContinuousImprovementExecution, execution_id)
    if not ex: raise ValueError('execution_not_found')
    decision=db.get(ContinuousImprovementAssignmentDecision, ex.decision_id)
    rec=db.get(ContinuousImprovementRecommendation, ex.recommendation_id)
    plan=db.get(ContinuousImprovementPlan, ex.plan_id)
    files=db.query(ContinuousImprovementExecutionEvidenceFile).filter_by(execution_id=execution_id).order_by(ContinuousImprovementExecutionEvidenceFile.id.asc()).all()
    events=db.query(ContinuousImprovementEvidenceIntegrityEvent).filter_by(execution_id=execution_id).order_by(ContinuousImprovementEvidenceIntegrityEvent.id.asc()).all()
    return {
      'schema':'v0.90','execution':{'id':ex.id,'status':ex.status,'decision_id':ex.decision_id,'recommendation_id':ex.recommendation_id,'plan_id':ex.plan_id,'assigned_to':ex.assigned_to,'started_at':ex.started_at,'completed_at':ex.completed_at,'verified_by':ex.verified_by,'verified_at':ex.verified_at,'execution_hash':ex.execution_hash,'evidence_manifest_hash':ex.evidence_manifest_hash,'resolution_note':ex.resolution_note,'evidence_note':ex.evidence_note,'verification_note':ex.verification_note},
      'decision': None if not decision else {'id':decision.id,'snapshot_id':decision.snapshot_id,'recommendation_id':decision.recommendation_id,'decision':decision.decision,'target_user_id':decision.target_user_id,'decided_by':decision.decided_by,'decided_at':decision.decided_at,'decision_note':decision.decision_note,'decision_hash':decision.decision_hash},
      'recommendation': None if not rec else {'id':rec.id,'indicator_code':rec.indicator_code,'pattern_code':rec.pattern_code,'status':rec.status,'decision':rec.decision,'decided_by':rec.decided_by,'decided_at':rec.decided_at,'implemented_by':rec.implemented_by,'implemented_at':rec.implemented_at,'integrity_hash':rec.integrity_hash},
      'plan': None if not plan else {'id':plan.id,'recommendation_id':plan.recommendation_id,'status':plan.status,'assigned_to':plan.assigned_to,'due_at':plan.due_at,'implemented_at':plan.implemented_at,'closed_at':plan.closed_at,'integrity_hash':plan.integrity_hash},
      'evidence_files':[{'id':f.id,'version':f.version,'original_name':f.original_name,'content_type':f.content_type,'size_bytes':f.size_bytes,'sha256':f.sha256,'uploaded_by':f.uploaded_by,'created_at':f.created_at,'revoked_at':f.revoked_at} for f in files],
      'integrity_events':[{'id':e.id,'file_id':e.file_id,'status':e.status,'expected_sha256':e.expected_sha256,'observed_sha256':e.observed_sha256,'actor_id':e.actor_id,'previous_event_hash':e.previous_event_hash,'event_hash':e.event_hash,'created_at':e.created_at,'details':e.details} for e in events]
    }

def certify(db: Session, execution_id: int, actor_id: int, note: str):
    if not _admin(db,actor_id): raise ValueError('actor_not_eligible')
    if not note or len(note.strip())<3: raise ValueError('certification_note_required')
    ex=db.get(ContinuousImprovementExecution, execution_id)
    if not ex: raise ValueError('execution_not_found')
    if ex.status!='VERIFIED': raise ValueError('execution_must_be_verified')
    if ex.assigned_to==actor_id or ex.verified_by==actor_id: raise ValueError('independent_certifier_required')
    if db.query(ContinuousImprovementCertification).filter_by(execution_id=execution_id).first(): raise ValueError('already_certified')
    evidence=verify_execution_evidence(db,execution_id,actor_id)
    if not evidence['valid']: raise ValueError('evidence_integrity_failed')
    chain=verify_chain(db)
    if not chain['valid']: raise ValueError('integrity_chain_failed')
    package=build_package(db,execution_id)
    package['certification']={'note':note.strip(),'certified_by':actor_id}
    package_hash=digest(package)
    certificate_id='FRC90-'+uuid.uuid4().hex.upper()
    row=ContinuousImprovementCertification(execution_id=execution_id,certificate_id=certificate_id,status='CERTIFIED',package_json=canonical(package),package_hash=package_hash,certified_by=actor_id,certified_at=now(),certification_note=note.strip(),created_at=now())
    db.add(row); db.flush()
    db.add(AuditLog(actor_user_id=actor_id,action='IMPROVEMENT_EXECUTION_CERTIFIED',entity_type='ContinuousImprovementCertification',entity_id=str(row.id),details=canonical({'certificate_id':certificate_id,'execution_id':execution_id,'package_hash':package_hash})))
    return row

def verify_certificate(db: Session, certificate_id: int):
    row=db.get(ContinuousImprovementCertification,certificate_id)
    if not row: raise ValueError('certificate_not_found')
    data=json.loads(row.package_json)
    # The package hash is the immutable certification fingerprint. It intentionally
    # includes the certification note/actor and the complete decision/execution/evidence chain.
    valid=digest(data)==row.package_hash and row.status=='CERTIFIED'
    return {'id':row.id,'certificate_id':row.certificate_id,'status':row.status,'valid':valid,'package_hash':row.package_hash,'certified_by':row.certified_by,'certified_at':row.certified_at.isoformat()}
