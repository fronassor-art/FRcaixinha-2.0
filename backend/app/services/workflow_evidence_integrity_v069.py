from __future__ import annotations
import hashlib, json
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import WorkflowExecutionEvidenceFile, WorkflowEvidenceIntegrityEvent, WorkflowExecutionEvidence
from app.services.workflow_evidence_storage_v068 import _storage_path

def utcnow(): return datetime.now(timezone.utc)

def _canonical(payload): return json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)

def _last_hash(db):
    row = db.query(WorkflowEvidenceIntegrityEvent).order_by(WorkflowEvidenceIntegrityEvent.id.desc()).first()
    return row.event_hash if row else None

def _record(db, file_row, status, observed, actor_id=None, details=None):
    prev=_last_hash(db); created=utcnow()
    payload={'file_id':file_row.id,'task_id':db.get(WorkflowExecutionEvidence,file_row.evidence_id).task_id,'event_type':'VERIFY','expected_sha256':file_row.sha256,'observed_sha256':observed,'status':status,'actor_id':actor_id,'previous_event_hash':prev,'details':details,'created_at':created.isoformat()}
    event_hash=hashlib.sha256(_canonical(payload).encode()).hexdigest()
    row=WorkflowEvidenceIntegrityEvent(file_id=file_row.id,task_id=payload['task_id'],event_type='VERIFY',expected_sha256=file_row.sha256,observed_sha256=observed,status=status,actor_id=actor_id,previous_event_hash=prev,event_hash=event_hash,details=details,created_at=created)
    db.add(row); db.flush(); return row

def verify_file(db: Session, file_id:int, actor_id=None):
    row=db.get(WorkflowExecutionEvidenceFile,file_id)
    if not row: raise ValueError('Arquivo não encontrado.')
    if row.revoked_at: return _record(db,row,'REVOKED',None,actor_id,'Arquivo revogado.')
    path=_storage_path(row.storage_key)
    if not path.is_file(): return _record(db,row,'MISSING',None,actor_id,'Arquivo físico ausente.')
    digest=hashlib.sha256();
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): digest.update(chunk)
    observed=digest.hexdigest(); status='PASS' if observed==row.sha256 else 'MISMATCH'
    return _record(db,row,status,observed,actor_id,None if status=='PASS' else 'SHA-256 diferente do registrado.')

def verify_all(db: Session, actor_id=None):
    rows=db.query(WorkflowExecutionEvidenceFile).order_by(WorkflowExecutionEvidenceFile.id.asc()).all()
    counts={'PASS':0,'MISMATCH':0,'MISSING':0,'REVOKED':0}; events=[]
    for row in rows:
        ev=verify_file(db,row.id,actor_id); counts[ev.status]=counts.get(ev.status,0)+1; events.append(ev.id)
    return {'checked':len(rows),'counts':counts,'event_ids':events}

def verify_chain(db: Session):
    rows=db.query(WorkflowEvidenceIntegrityEvent).order_by(WorkflowEvidenceIntegrityEvent.id.asc()).all(); prev=None
    failures=[]
    for row in rows:
        payload={'file_id':row.file_id,'task_id':row.task_id,'event_type':row.event_type,'expected_sha256':row.expected_sha256,'observed_sha256':row.observed_sha256,'status':row.status,'actor_id':row.actor_id,'previous_event_hash':row.previous_event_hash,'details':row.details,'created_at':row.created_at.isoformat()}
        calc=hashlib.sha256(_canonical(payload).encode()).hexdigest()
        if row.previous_event_hash != prev or row.event_hash != calc: failures.append(row.id)
        prev=row.event_hash
    return {'events':len(rows),'valid':not failures,'failures':failures,'head_hash':prev}
