from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import OperationalWorkflowTask, OperationalActionRecord, AuditLog
from app.services.workflow_sla_v063 import enrich_task_sla, apply_sla

LEVEL_RANK = {'NONE': 0, 'HIGH': 1, 'CRITICAL': 2}
PRIORITY_LEVEL = {'LOW': 'NONE', 'MEDIUM': 'HIGH', 'HIGH': 'HIGH', 'CRITICAL': 'CRITICAL'}


def utcnow():
    return datetime.now(timezone.utc)


def escalation_level(task, now=None):
    info = enrich_task_sla(task, now)
    if not info['overdue'] or task.status == 'COMPLETED':
        return 'NONE'
    return PRIORITY_LEVEL.get(task.priority, 'HIGH')


def sync_workflow_escalations(db: Session, actor_id: int | None = None, now=None):
    """Escalate overdue workflow tasks without changing any financial record."""
    now = now or utcnow()
    rows = db.query(OperationalWorkflowTask).filter(OperationalWorkflowTask.status != 'COMPLETED').all()
    created = updated = 0
    items = []
    for task in rows:
        apply_sla(db, task, actor_id=actor_id, now=now)
        level = escalation_level(task, now)
        if level == 'NONE':
            continue
        previous = task.escalation_level or 'NONE'
        changed = LEVEL_RANK[level] > LEVEL_RANK.get(previous, 0)
        if changed:
            task.escalation_level = level
            task.escalated_at = now
            db.add(AuditLog(actor_user_id=actor_id, action='WORKFLOW_ESCALATED',
                            entity_type='OperationalWorkflowTask', entity_id=str(task.id),
                            details=f'action_code={task.action_code};from={previous};to={level}'))
        action = (db.query(OperationalActionRecord)
                  .filter(OperationalActionRecord.source_task_id == task.id,
                          OperationalActionRecord.status == 'OPEN')
                  .order_by(OperationalActionRecord.id.desc()).first())
        note = f'Escalonamento automático v0.64: tarefa #{task.id} vencida; nível={level}; prioridade={task.priority}.'
        if action is None:
            action = OperationalActionRecord(action_code='WORKFLOW_ESCALATION', status='OPEN',
                                             assigned_to=task.assigned_to, note=note,
                                             source_task_id=task.id, escalation_level=level)
            db.add(action); created += 1
            db.add(AuditLog(actor_user_id=actor_id, action='WORKFLOW_ESCALATION_ACTION_CREATED',
                            entity_type='OperationalActionRecord', entity_id=str(task.id), details=note))
        elif LEVEL_RANK[level] > LEVEL_RANK.get(action.escalation_level or 'NONE', 0):
            action.escalation_level = level
            action.assigned_to = task.assigned_to
            action.note = note
            action.updated_at = now
            updated += 1
            db.add(AuditLog(actor_user_id=actor_id, action='WORKFLOW_ESCALATION_ACTION_UPDATED',
                            entity_type='OperationalActionRecord', entity_id=str(action.id), details=note))
        items.append({'task_id': task.id, 'action_id': action.id if action.id else None,
                      'escalation_level': level, 'changed': changed})
    db.flush()
    return {'open_tasks': len(rows), 'escalated': len(items), 'actions_created': created,
            'actions_updated': updated, 'items': items}
