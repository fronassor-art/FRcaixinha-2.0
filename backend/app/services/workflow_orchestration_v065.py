from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import OperationalWorkflowTask, OperationalWorkflowOrchestration, AuditLog
from app.services.workflow_sla_v063 import apply_sla, enrich_task_sla
from app.services.workflow_escalation_v064 import escalation_level, LEVEL_RANK

QUEUE_STATUS = ('READY','ASSIGNED','IN_PROGRESS','ESCALATED','COMPLETED')
PRIORITY_SCORE = {'LOW': 10, 'MEDIUM': 30, 'HIGH': 60, 'CRITICAL': 100}
SLA_SCORE = {'ON_TRACK': 0, 'OVERDUE': 40, 'COMPLETED': 0}
ESC_SCORE = {'NONE': 0, 'HIGH': 30, 'CRITICAL': 60}

def utcnow():
    return datetime.now(timezone.utc)

def orchestration_score(task, sla_status, escalation):
    return PRIORITY_SCORE.get(task.priority, 30) + SLA_SCORE.get(sla_status, 0) + ESC_SCORE.get(escalation, 0)

def queue_status(task, escalation):
    if task.status == 'COMPLETED': return 'COMPLETED'
    if escalation != 'NONE': return 'ESCALATED'
    if task.status == 'IN_EXECUTION': return 'IN_PROGRESS'
    if task.assigned_to is not None or task.status == 'ASSIGNED': return 'ASSIGNED'
    return 'READY'

def sync_workflow_orchestration(db: Session, actor_id: int | None = None, now=None):
    """Build the operational execution queue from workflow status + SLA + escalation.
    This never mutates financial records and never auto-assigns an administrator.
    """
    now = now or utcnow()
    tasks = db.query(OperationalWorkflowTask).all()
    created = updated = escalated = 0
    items=[]
    for task in tasks:
        apply_sla(db, task, actor_id=actor_id, now=now)
        info = enrich_task_sla(task, now)
        esc = escalation_level(task, now)
        qstatus = queue_status(task, esc)
        score = orchestration_score(task, info['sla_status'], esc)
        row = db.query(OperationalWorkflowOrchestration).filter_by(task_id=task.id).first()
        if row is None:
            row = OperationalWorkflowOrchestration(task_id=task.id, queue_status=qstatus,
                assigned_to=task.assigned_to, priority=task.priority, sla_status=info['sla_status'],
                escalation_level=esc, orchestration_score=score, last_evaluated_at=now,
                created_at=now, updated_at=now)
            db.add(row); db.flush(); created += 1
            db.add(AuditLog(actor_user_id=actor_id, action='WORKFLOW_ORCHESTRATION_CREATED',
                entity_type='OperationalWorkflowOrchestration', entity_id=str(row.id),
                details=f'task={task.id};queue={qstatus};score={score}'))
        else:
            changed = (row.queue_status != qstatus or row.assigned_to != task.assigned_to or
                       row.priority != task.priority or row.sla_status != info['sla_status'] or
                       LEVEL_RANK.get(esc,0) > LEVEL_RANK.get(row.escalation_level or 'NONE',0) or
                       row.orchestration_score != score)
            if changed:
                oldq=row.queue_status; olde=row.escalation_level
                row.queue_status=qstatus; row.assigned_to=task.assigned_to; row.priority=task.priority
                row.sla_status=info['sla_status']; row.escalation_level=esc; row.orchestration_score=score
                row.updated_at=now; updated += 1
                db.add(AuditLog(actor_user_id=actor_id, action='WORKFLOW_ORCHESTRATION_UPDATED',
                    entity_type='OperationalWorkflowOrchestration', entity_id=str(row.id),
                    details=f'task={task.id};queue={oldq}->{qstatus};escalation={olde}->{esc};score={score}'))
        row.last_evaluated_at=now
        if esc != 'NONE': escalated += 1
        items.append({'task_id':task.id,'orchestration_id':row.id,'queue_status':qstatus,
                      'assigned_to':task.assigned_to,'priority':task.priority,
                      'sla_status':info['sla_status'],'escalation_level':esc,'score':score})
    db.flush()
    return {'tasks':len(tasks),'created':created,'updated':updated,'escalated':escalated,'items':items}
