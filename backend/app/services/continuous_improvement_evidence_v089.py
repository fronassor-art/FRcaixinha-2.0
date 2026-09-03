from __future__ import annotations
import hashlib, json, re, uuid
from pathlib import Path
from datetime import datetime, timezone
from fastapi import UploadFile
from sqlalchemy.orm import Session
from app.core.config import settings
from app.models import AuditLog, ContinuousImprovementExecution, ContinuousImprovementExecutionEvidenceFile, ContinuousImprovementEvidenceIntegrityEvent

ALLOWED_EXTENSIONS={'.pdf','.jpg','.jpeg','.png','.txt','.csv','.zip'}
SAFE_NAME_RE=re.compile(r'[^A-Za-z0-9._-]+')

def utcnow(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,default=str)
def digest(v): return hashlib.sha256(canonical(v).encode()).hexdigest()
def sanitize_filename(name):
    raw=(name or 'evidencia').replace('\\','/').split('/')[-1]
    raw=SAFE_NAME_RE.sub('_',raw).strip('._')
    return raw[:255] or 'evidencia'
def storage_path(key):
    root=(Path(settings.workflow_evidence_storage_root).expanduser().resolve()/'continuous-improvement').resolve()
    candidate=(root/key).resolve()
    if root!=candidate.parent and root not in candidate.parents: raise ValueError('invalid_storage_key')
    return candidate
def validate(filename,ctype):
    name=sanitize_filename(filename); ext=Path(name).suffix.lower(); c=(ctype or 'application/octet-stream').lower().split(';')[0].strip()
    allowed={x.strip().lower() for x in settings.workflow_evidence_allowed_types.split(',') if x.strip()}
    if ext not in ALLOWED_EXTENSIONS: raise ValueError('file_extension_not_allowed')
    if c not in allowed: raise ValueError('mime_type_not_allowed')
    return name,c
def _eligible(db,uid):
    u=db.get(__import__('app.models',fromlist=['User']).User,uid)
    return bool(u and u.role=='ADMIN' and u.is_active)
def _last_event(db):
    return db.query(ContinuousImprovementEvidenceIntegrityEvent).order_by(ContinuousImprovementEvidenceIntegrityEvent.id.desc()).first()
def _event(db,row,status,observed,actor_id,details=None):
    prev=_last_event(db); prev_hash=prev.event_hash if prev else None; created=utcnow()
    payload={'file_id':row.id,'execution_id':row.execution_id,'event_type':'VERIFY','expected_sha256':row.sha256,'observed_sha256':observed,'status':status,'actor_id':actor_id,'previous_event_hash':prev_hash,'details':details,'created_at':created.isoformat()}
    h=hashlib.sha256(canonical(payload).encode()).hexdigest()
    ev=ContinuousImprovementEvidenceIntegrityEvent(file_id=row.id,execution_id=row.execution_id,event_type='VERIFY',expected_sha256=row.sha256,observed_sha256=observed,status=status,actor_id=actor_id,previous_event_hash=prev_hash,event_hash=h,details=details,created_at=created)
    db.add(ev); db.flush(); return ev

def _manifest(db,execution_id):
    rows=db.query(ContinuousImprovementExecutionEvidenceFile).filter_by(execution_id=execution_id).order_by(ContinuousImprovementExecutionEvidenceFile.id.asc()).all()
    items=[{'id':r.id,'version':r.version,'original_name':r.original_name,'size_bytes':r.size_bytes,'sha256':r.sha256} for r in rows]
    return items,digest(items)

def _refresh_execution_hash(db,execution):
    _,mh=_manifest(db,execution.id); execution.evidence_manifest_hash=mh; execution.updated_at=utcnow()
    payload={'decision_id':execution.decision_id,'recommendation_id':execution.recommendation_id,'plan_id':execution.plan_id,'assigned_to':execution.assigned_to,'status':execution.status,'started_at':execution.started_at,'completed_at':execution.completed_at,'verified_by':execution.verified_by,'verified_at':execution.verified_at,'resolution_note':execution.resolution_note,'evidence_note':execution.evidence_note,'verification_note':execution.verification_note,'evidence_manifest_hash':mh}
    execution.execution_hash=digest(payload)

def upload_file(db:Session,execution_id:int,actor_id:int,upload:UploadFile):
    execution=db.get(ContinuousImprovementExecution,execution_id)
    if not execution: raise ValueError('execution_not_found')
    if not _eligible(db,actor_id): raise ValueError('actor_not_eligible')
    if actor_id!=execution.assigned_to: raise ValueError('only_assignee_can_upload')
    if execution.status not in ('IN_EXECUTION','COMPLETED'): raise ValueError('invalid_state_for_upload')
    name,ctype=validate(upload.filename,upload.content_type); max_bytes=int(settings.workflow_evidence_max_bytes)
    key=f'execution/{execution.id}/{uuid.uuid4().hex}.bin'; path=storage_path(key); path.parent.mkdir(parents=True,exist_ok=True)
    size=0; h=hashlib.sha256()
    try:
        with path.open('wb') as out:
            while True:
                chunk=upload.file.read(1024*1024)
                if not chunk: break
                size+=len(chunk)
                if size>max_bytes: raise ValueError(f'file_too_large_{max_bytes}')
                h.update(chunk); out.write(chunk)
    except Exception:
        path.unlink(missing_ok=True); raise
    latest=db.query(ContinuousImprovementExecutionEvidenceFile).filter_by(execution_id=execution.id).order_by(ContinuousImprovementExecutionEvidenceFile.version.desc()).first()
    row=ContinuousImprovementExecutionEvidenceFile(execution_id=execution.id,version=(latest.version+1 if latest else 1),original_name=name,storage_key=key,content_type=ctype,size_bytes=size,sha256=h.hexdigest(),uploaded_by=actor_id,created_at=utcnow())
    db.add(row); db.flush(); _refresh_execution_hash(db,execution)
    db.add(AuditLog(actor_user_id=actor_id,action='IMPROVEMENT_EXECUTION_EVIDENCE_UPLOADED',entity_type='ContinuousImprovementExecution',entity_id=str(execution.id),details=canonical({'file_id':row.id,'sha256':row.sha256,'size_bytes':size,'execution_hash':execution.execution_hash})))
    return row

def list_files(db,execution_id):
    rows=db.query(ContinuousImprovementExecutionEvidenceFile).filter_by(execution_id=execution_id).order_by(ContinuousImprovementExecutionEvidenceFile.id.asc()).all()
    return [{'id':r.id,'version':r.version,'original_name':r.original_name,'content_type':r.content_type,'size_bytes':r.size_bytes,'sha256':r.sha256,'uploaded_by':r.uploaded_by,'created_at':r.created_at.isoformat(),'revoked_at':r.revoked_at.isoformat() if r.revoked_at else None} for r in rows]

def get_file(db,file_id):
    row=db.get(ContinuousImprovementExecutionEvidenceFile,file_id)
    if not row or row.revoked_at: raise ValueError('file_not_found_or_revoked')
    path=storage_path(row.storage_key)
    if not path.is_file(): raise ValueError('physical_file_missing')
    return row,path

def verify_execution_evidence(db,execution_id,actor_id):
    execution=db.get(ContinuousImprovementExecution,execution_id)
    if not execution: raise ValueError('execution_not_found')
    if not _eligible(db,actor_id): raise ValueError('actor_not_eligible')
    rows=db.query(ContinuousImprovementExecutionEvidenceFile).filter_by(execution_id=execution_id).order_by(ContinuousImprovementExecutionEvidenceFile.id.asc()).all()
    if not rows: raise ValueError('evidence_file_required')
    counts={'PASS':0,'MISMATCH':0,'MISSING':0,'REVOKED':0}
    for row in rows:
        if row.revoked_at: status='REVOKED'; observed=None
        else:
            path=storage_path(row.storage_key)
            if not path.is_file(): status='MISSING'; observed=None
            else:
                h=hashlib.sha256()
                with path.open('rb') as f:
                    for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
                observed=h.hexdigest(); status='PASS' if observed==row.sha256 else 'MISMATCH'
        counts[status]+=1; _event(db,row,status,observed,actor_id,None if status=='PASS' else 'Arquivo não corresponde ao hash registrado.')
    items,mh=_manifest(db,execution_id)
    ok=counts['MISMATCH']==0 and counts['MISSING']==0 and counts['REVOKED']==0
    if execution.evidence_manifest_hash!=mh: ok=False
    return {'execution_id':execution_id,'checked':len(rows),'counts':counts,'manifest_hash':mh,'stored_manifest_hash':execution.evidence_manifest_hash,'valid':ok}

def verify_chain(db):
    rows=db.query(ContinuousImprovementEvidenceIntegrityEvent).order_by(ContinuousImprovementEvidenceIntegrityEvent.id.asc()).all(); prev=None; failures=[]
    for row in rows:
        payload={'file_id':row.file_id,'execution_id':row.execution_id,'event_type':row.event_type,'expected_sha256':row.expected_sha256,'observed_sha256':row.observed_sha256,'status':row.status,'actor_id':row.actor_id,'previous_event_hash':row.previous_event_hash,'details':row.details,'created_at':row.created_at.isoformat()}
        calc=hashlib.sha256(canonical(payload).encode()).hexdigest()
        if row.previous_event_hash!=prev or row.event_hash!=calc: failures.append(row.id)
        prev=row.event_hash
    return {'events':len(rows),'valid':not failures,'failures':failures,'head_hash':prev}
