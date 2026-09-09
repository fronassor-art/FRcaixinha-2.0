from __future__ import annotations
from datetime import datetime, timezone, timedelta
import hashlib, json
from sqlalchemy.orm import Session
from app.models import OperationalRiskAlert, OperationalRiskResponsePlan, AuditLog
from app.services.admin_workflow_v062 import create_task

PRIORITY = {'CRITICAL':'CRITICAL','HIGH':'HIGH','ATTENTION':'MEDIUM'}
def utcnow(): return datetime.now(timezone.utc)
def _hash(p): return hashlib.sha256(json.dumps(p,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()

def _plan_text(alert):
    return f'Analisar o alerta {alert.id} ({alert.alert_type}), validar a causa, revisar os controles afetados e registrar evidências antes do encerramento. Recomendação: {alert.recommended_action}'

def sync_response_plans(db: Session, actor_id=None, now=None):
    now=now or utcnow(); created=updated=0; items=[]
    alerts=db.query(OperationalRiskAlert).filter(OperationalRiskAlert.status.in_(['OPEN','ACKNOWLEDGED'])).order_by(OperationalRiskAlert.created_at.desc()).all()
    for alert in alerts:
        plan=db.query(OperationalRiskResponsePlan).filter_by(alert_id=alert.id).first()
        if plan: updated+=1; items.append(plan); continue
        priority=PRIORITY.get(alert.severity,'MEDIUM')
        due=now+timedelta(hours={'CRITICAL':4,'HIGH':12,'MEDIUM':24}[priority])
        task=None
        if actor_id is not None:
            task=create_task(db, action_code=f'RISK_ALERT_{alert.id}', actor_id=actor_id, priority=priority, due_at=due, description=_plan_text(alert))
        payload={'alert_id':alert.id,'priority':priority,'due_at':due,'task_id':task.id if task else None,'plan':_plan_text(alert)}
        plan=OperationalRiskResponsePlan(alert_id=alert.id,status='OPEN',priority=priority,workflow_task_id=task.id if task else None,due_at=due,plan=payload['plan'],integrity_hash=_hash(payload),created_at=now,updated_at=now)
        db.add(plan); db.flush(); created+=1; items.append(plan)
        db.add(AuditLog(actor_user_id=actor_id,action='RISK_ALERT_RESPONSE_PLAN_CREATED',entity_type='OperationalRiskResponsePlan',entity_id=str(plan.id),details=f'alert={alert.id};task={task.id if task else None}'))
    return {'created':created,'updated':updated,'items':[{'id':p.id,'alert_id':p.alert_id,'status':p.status,'priority':p.priority,'assigned_to':p.assigned_to,'workflow_task_id':p.workflow_task_id,'due_at':p.due_at.isoformat() if p.due_at else None} for p in items]}

def assign_plan(db, plan, actor_id, assigned_to, now=None):
    now=now or utcnow(); plan.assigned_to=assigned_to; plan.status='IN_PROGRESS'; plan.updated_at=now
    db.add(AuditLog(actor_user_id=actor_id,action='RISK_RESPONSE_ASSIGNED',entity_type='OperationalRiskResponsePlan',entity_id=str(plan.id),details=f'assigned_to={assigned_to}')); return plan

def verify_plan(db, plan, actor_id, evidence_note, resolution, now=None):
    if not evidence_note or not resolution: raise ValueError('Verificação exige evidência e resolução.')
    now=now or utcnow(); plan.status='VERIFIED'; plan.verified_by=actor_id; plan.verified_at=now; plan.evidence_note=evidence_note; plan.resolution=resolution; plan.updated_at=now
    plan.integrity_hash=_hash({'id':plan.id,'alert_id':plan.alert_id,'status':plan.status,'evidence_note':evidence_note,'resolution':resolution,'verified_by':actor_id,'verified_at':now})
    alert=db.get(OperationalRiskAlert, plan.alert_id)
    if alert and alert.status != 'RESOLVED':
        alert.status='RESOLVED'; alert.resolved_at=now; alert.updated_at=now
    db.add(AuditLog(actor_user_id=actor_id,action='RISK_RESPONSE_VERIFIED',entity_type='OperationalRiskResponsePlan',entity_id=str(plan.id),details='Resposta verificada com evidência.')); return plan
