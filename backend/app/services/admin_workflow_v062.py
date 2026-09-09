from __future__ import annotations
from datetime import datetime, timezone
import hashlib, json
from sqlalchemy.orm import Session
from app.models import OperationalWorkflowTask, OperationalWorkflowEvent, AuditLog

STATUSES=('PENDING','IN_ANALYSIS','ASSIGNED','IN_EXECUTION','COMPLETED')
PRIORITIES=('LOW','MEDIUM','HIGH','CRITICAL')

def _hash(payload):
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()

def create_task(db:Session, *, action_code:str, actor_id:int, priority='MEDIUM', assigned_to=None, due_at=None, description=None):
    if priority not in PRIORITIES: raise ValueError('Prioridade inválida.')
    task=OperationalWorkflowTask(action_code=action_code,status='PENDING',priority=priority,assigned_to=assigned_to,due_at=due_at,description=description,created_by=actor_id)
    db.add(task); db.flush()
    db.add(AuditLog(actor_user_id=actor_id,action='WORKFLOW_TASK_CREATED',entity_type='OPERATIONAL_WORKFLOW',entity_id=str(task.id),details=action_code))
    db.flush(); return task

def transition(db:Session, task:OperationalWorkflowTask, *, actor_id:int, status:str, note=None, evidence=None, assigned_to=None):
    status=status.upper()
    if status not in STATUSES: raise ValueError('Status de workflow inválido.')
    allowed={'PENDING':{'IN_ANALYSIS','ASSIGNED'},'IN_ANALYSIS':{'ASSIGNED','IN_EXECUTION','COMPLETED'},'ASSIGNED':{'IN_ANALYSIS','IN_EXECUTION'},'IN_EXECUTION':{'IN_ANALYSIS','COMPLETED'}}
    if status not in allowed.get(task.status,set()): raise ValueError(f'Transição não permitida: {task.status} -> {status}.')
    if status=='COMPLETED' and not (note or evidence): raise ValueError('Conclusão exige comentário ou evidência.')
    old_status=task.status
    task.status=status; task.updated_at=datetime.now(timezone.utc)
    if assigned_to is not None: task.assigned_to=assigned_to
    db.add(OperationalWorkflowEvent(task_id=task.id,actor_id=actor_id,from_status=old_status,to_status=status,note=note,evidence=evidence,event_hash='pending'))
    db.flush(); ev=db.query(OperationalWorkflowEvent).filter_by(task_id=task.id).order_by(OperationalWorkflowEvent.id.desc()).first()
    payload={'task_id':task.id,'actor_id':actor_id,'from_status':ev.from_status,'to_status':status,'note':note,'evidence':evidence,'created_at':ev.created_at}
    ev.event_hash=_hash(payload); db.add(AuditLog(actor_user_id=actor_id,action='WORKFLOW_TASK_TRANSITION',entity_type='OPERATIONAL_WORKFLOW',entity_id=str(task.id),details=f'{ev.from_status}->{status}'))
    db.flush(); return task
