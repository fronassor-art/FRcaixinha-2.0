from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from app.models import OperationalWorkflowTask, WorkflowComplianceSnapshot, AuditLog, WorkflowIncident
from app.services.workflow_compliance_v070 import build_compliance

SEVERITY_RANK={'ATTENTION':1,'CRITICAL':2}

def utcnow(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False)

def sync_incidents(db:Session, actor_id:int|None=None, now=None):
    now=now or utcnow(); compliance=build_compliance(db,now)
    created=updated=0; items=[]
    for check in compliance['checks']:
        if check['status'] not in SEVERITY_RANK: continue
        code=check['name']; sev=check['status']
        inc=(db.query(WorkflowIncident).filter(WorkflowIncident.check_code==code, WorkflowIncident.status!='CLOSED').order_by(WorkflowIncident.id.desc()).first())
        if inc is None:
            due=now + timedelta(hours=24 if sev=='ATTENTION' else 8)
            inc=WorkflowIncident(check_code=code,severity=sev,status='OPEN',title=f'Não conformidade: {code}',description=json.dumps(check['details'],ensure_ascii=False),due_at=due,opened_at=now,created_at=now,updated_at=now)
            db.add(inc); db.flush(); created+=1
            db.add(AuditLog(actor_user_id=actor_id,action='WORKFLOW_INCIDENT_CREATED',entity_type='WorkflowIncident',entity_id=str(inc.id),details=f'check={code};severity={sev}'))
        elif SEVERITY_RANK[sev] > SEVERITY_RANK.get(inc.severity,0):
            old=inc.severity; inc.severity=sev; inc.updated_at=now; updated+=1
            db.add(AuditLog(actor_user_id=actor_id,action='WORKFLOW_INCIDENT_ESCALATED',entity_type='WorkflowIncident',entity_id=str(inc.id),details=f'check={code};from={old};to={sev}'))
        items.append({'id':inc.id,'check_code':code,'severity':inc.severity,'status':inc.status,'assigned_to':inc.assigned_to,'due_at':inc.due_at.isoformat() if inc.due_at else None})
    db.flush(); return {'compliance_status':compliance['status'],'created':created,'updated':updated,'items':items}

def assign_incident(db, inc, user_id, actor_id, now=None):
    now=now or utcnow(); inc.assigned_to=user_id; inc.status='IN_REVIEW'; inc.updated_at=now
    db.add(AuditLog(actor_user_id=actor_id,action='WORKFLOW_INCIDENT_ASSIGNED',entity_type='WorkflowIncident',entity_id=str(inc.id),details=f'assigned_to={user_id}'))

def remediate_incident(db,inc,actor_id,note,now=None):
    if not note or not note.strip(): raise ValueError('Plano de correção obrigatório.')
    now=now or utcnow(); inc.remediation_plan=note.strip(); inc.status='REMEDIATION'; inc.updated_at=now
    db.add(AuditLog(actor_user_id=actor_id,action='WORKFLOW_INCIDENT_REMEDIATION',entity_type='WorkflowIncident',entity_id=str(inc.id),details='plano de correção registrado'))

def close_incident(db,inc,actor_id,resolution,now=None):
    if inc.status=='CLOSED': raise ValueError('Não conformidade já encerrada.')
    if not resolution or not resolution.strip(): raise ValueError('Resolução obrigatória.')
    if not inc.remediation_plan: raise ValueError('Plano de correção obrigatório antes do encerramento.')
    now=now or utcnow(); current=build_compliance(db,now)
    check=next((c for c in current['checks'] if c['name']==inc.check_code),None)
    if check and check['status']!='PASS': raise ValueError('A não conformidade só pode ser encerrada quando o controle estiver PASS.')
    inc.resolution=resolution.strip(); inc.status='CLOSED'; inc.closed_at=now; inc.updated_at=now
    db.add(AuditLog(actor_user_id=actor_id,action='WORKFLOW_INCIDENT_CLOSED',entity_type='WorkflowIncident',entity_id=str(inc.id),details=f'check={inc.check_code}'))

def incident_hash(inc):
    return hashlib.sha256(canonical({'id':inc.id,'check_code':inc.check_code,'severity':inc.severity,'status':inc.status,'assigned_to':inc.assigned_to,'remediation_plan':inc.remediation_plan,'resolution':inc.resolution}).encode()).hexdigest()
