from __future__ import annotations
import hashlib, json
from datetime import date, datetime, timezone
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models import (ExecutiveRiskResponseSnapshot, OperationalRiskTrendSnapshot, OperationalRiskAlert,
    OperationalRiskResponsePlan, WorkflowIncident, CorrectiveActionPlan, OperationalWorkflowTask)

def utcnow(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
def _status(score): return 'CRITICAL' if score >= 70 else ('ATTENTION' if score >= 30 else 'PASS')

def build_dashboard(db: Session, today: date|None=None):
    today=today or date.today()
    risk=db.query(OperationalRiskTrendSnapshot).order_by(OperationalRiskTrendSnapshot.snapshot_date.desc()).first()
    risk_data=json.loads(risk.snapshot_json) if risk else {'risk_score':0,'status':'PASS','current':{},'trends':[]}
    alerts=db.query(OperationalRiskAlert).filter(OperationalRiskAlert.status!='RESOLVED').all()
    responses=db.query(OperationalRiskResponsePlan).filter(OperationalRiskResponsePlan.status.notin_(['VERIFIED'])).all()
    incidents=db.query(WorkflowIncident).filter(WorkflowIncident.status!='CLOSED').all()
    capas=db.query(CorrectiveActionPlan).filter(CorrectiveActionPlan.status.notin_(['CLOSED'])).all()
    tasks=db.query(OperationalWorkflowTask).filter(OperationalWorkflowTask.status.notin_(['COMPLETED'])).all()
    now=utcnow()
    overdue_responses=sum(1 for x in responses if x.due_at and x.due_at < now)
    overdue_capas=sum(1 for x in capas if x.due_at and x.due_at < now)
    overdue_tasks=sum(1 for x in tasks if x.due_at and x.due_at < now)
    owners={}
    for x in responses:
        if x.assigned_to: owners[str(x.assigned_to)]=owners.get(str(x.assigned_to),0)+1
    severity={'CRITICAL':0,'HIGH':0,'ATTENTION':0}
    for x in alerts: severity[x.severity]=severity.get(x.severity,0)+1
    response_status={}
    for x in responses: response_status[x.status]=response_status.get(x.status,0)+1
    flags=[]
    if risk_data.get('risk_score',0)>=70: flags.append('RISK_CRITICAL')
    if severity['CRITICAL']>0: flags.append('CRITICAL_ALERTS')
    if overdue_responses>0: flags.append('RESPONSE_SLA_OVERDUE')
    if overdue_capas>0: flags.append('CAPA_OVERDUE')
    if overdue_tasks>0: flags.append('WORKFLOW_SLA_OVERDUE')
    if any(x.status=='REOPENED' for x in capas): flags.append('CAPA_REOPENED')
    if any(x.status in ('REMEDIATION','IN_REVIEW') for x in incidents): flags.append('INCIDENTS_IN_REMEDIATION')
    status='CRITICAL' if ('RISK_CRITICAL' in flags or severity['CRITICAL']>0) else ('ATTENTION' if flags else 'PASS')
    return {'schema':'v0.77','snapshot_date':today.isoformat(),'status':status,'risk':{'score':risk_data.get('risk_score',0),'status':risk_data.get('status','PASS'),'snapshot_id':risk.id if risk else None,'trends':risk_data.get('trends',[])},
      'alerts':{'open':len(alerts),'by_severity':severity},'responses':{'open':len(responses),'overdue':overdue_responses,'by_status':response_status,'unassigned':sum(1 for x in responses if not x.assigned_to)},
      'incidents':{'open':len(incidents),'in_remediation':sum(1 for x in incidents if x.status=='REMEDIATION')},
      'capas':{'open':len(capas),'overdue':overdue_capas,'reopened':sum(1 for x in capas if x.status=='REOPENED')},
      'workflow':{'open_tasks':len(tasks),'overdue':overdue_tasks},'owners':owners,'risk_flags':flags}

def persist_dashboard(db:Session, generated_by:int|None=None, today:date|None=None):
    today=today or date.today(); data=build_dashboard(db,today); raw=canonical(data); h=hashlib.sha256(raw.encode()).hexdigest()
    row=db.query(ExecutiveRiskResponseSnapshot).filter_by(snapshot_date=today).first()
    if row: row.status=data['status']; row.snapshot_json=raw; row.snapshot_hash=h; row.generated_by=generated_by
    else: row=ExecutiveRiskResponseSnapshot(snapshot_date=today,status=data['status'],snapshot_json=raw,snapshot_hash=h,generated_by=generated_by); db.add(row)
    db.flush(); return row,data
