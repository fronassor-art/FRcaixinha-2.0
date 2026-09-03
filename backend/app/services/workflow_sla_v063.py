from __future__ import annotations
from datetime import datetime, timezone, timedelta
from app.models import OperationalWorkflowTask, AuditLog

PRIORITY_HOURS={'CRITICAL':4,'HIGH':12,'MEDIUM':24,'LOW':72}

def utcnow(): return datetime.now(timezone.utc)

def sla_hours(priority): return PRIORITY_HOURS.get(priority.upper(),24)

def enrich_task_sla(task, now=None):
    now=now or utcnow()
    due=task.due_at
    if not due:
        due=task.created_at + timedelta(hours=sla_hours(task.priority))
    overdue=task.status!='COMPLETED' and due < now
    remaining=max(0,int((due-now).total_seconds())) if not overdue else 0
    return {'due_at':due.isoformat(), 'overdue':overdue, 'remaining_seconds':remaining,
            'sla_hours':sla_hours(task.priority), 'escalation_level': 'CRITICAL' if overdue and task.priority=='CRITICAL' else 'HIGH' if overdue else 'NONE'}

def apply_sla(db, task, actor_id=None, now=None):
    info=enrich_task_sla(task,now)
    if info['overdue'] and task.status!='COMPLETED' and getattr(task,'sla_status',None)!='OVERDUE':
        task.sla_status='OVERDUE'; task.escalated_at=now or utcnow(); task.escalation_level=info['escalation_level']
        if actor_id:
            db.add(AuditLog(actor_user_id=actor_id,action='WORKFLOW_SLA_OVERDUE',entity_type='OperationalWorkflowTask',entity_id=str(task.id),details=f'priority={task.priority};level={task.escalation_level}'))
    elif task.status=='COMPLETED':
        task.sla_status='COMPLETED'
    elif getattr(task,'sla_status',None) is None:
        task.sla_status='ON_TRACK'
    return info
