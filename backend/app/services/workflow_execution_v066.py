from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import OperationalWorkflowTask, OperationalWorkflowOrchestration, AuditLog
from app.services.admin_workflow_v062 import transition

STATES = ('PENDING_ACCEPTANCE','ACCEPTED','IN_EXECUTION','COMPLETED')

def utcnow():
    return datetime.now(timezone.utc)

def _get_orch(db: Session, task_id: int):
    row = db.query(OperationalWorkflowOrchestration).filter_by(task_id=task_id).first()
    if row is None:
        raise ValueError('Orquestração da tarefa não encontrada. Execute a sincronização operacional.')
    return row

def _audit(db, actor_id, action, task_id, details):
    db.add(AuditLog(actor_user_id=actor_id, action=action, entity_type='OperationalWorkflowTask', entity_id=str(task_id), details=details))

def accept(db: Session, task: OperationalWorkflowTask, actor_id: int, now=None):
    now = now or utcnow(); row = _get_orch(db, task.id)
    if task.status != 'ASSIGNED' or task.assigned_to != actor_id:
        raise ValueError('Somente o administrador responsável por uma tarefa ASSIGNED pode aceitá-la.')
    if row.execution_state != 'PENDING_ACCEPTANCE':
        raise ValueError(f'Tarefa não está aguardando aceite: {row.execution_state}.')
    row.execution_state='ACCEPTED'; row.accepted_by=actor_id; row.accepted_at=now; row.updated_at=now
    _audit(db, actor_id, 'WORKFLOW_EXECUTION_ACCEPTED', task.id, f'assigned_to={actor_id}')
    db.flush(); return row

def start(db: Session, task: OperationalWorkflowTask, actor_id: int, now=None):
    now = now or utcnow(); row = _get_orch(db, task.id)
    if row.execution_state != 'ACCEPTED' or row.accepted_by != actor_id:
        raise ValueError('A execução exige aceite prévio pelo administrador responsável.')
    if task.status != 'ASSIGNED':
        raise ValueError('A tarefa precisa estar ASSIGNED para iniciar a execução.')
    transition(db, task, actor_id=actor_id, status='IN_EXECUTION')
    row.execution_state='IN_EXECUTION'; row.started_by=actor_id; row.started_at=now; row.updated_at=now
    _audit(db, actor_id, 'WORKFLOW_EXECUTION_STARTED', task.id, f'accepted_by={row.accepted_by}')
    db.flush(); return row

def complete(db: Session, task: OperationalWorkflowTask, actor_id: int, note: str | None, evidence: str | None, now=None):
    now = now or utcnow(); row = _get_orch(db, task.id)
    if row.execution_state != 'IN_EXECUTION' or row.started_by != actor_id:
        raise ValueError('Somente o administrador que iniciou a execução pode concluí-la.')
    if not (note or evidence):
        raise ValueError('Conclusão exige comentário ou evidência.')
    from app.services.workflow_evidence_v067 import checklist_status
    check = checklist_status(db, task.id)
    if check['required_pending']:
        raise ValueError(f'Conclusão bloqueada: {check["required_pending"]} item(ns) obrigatório(s) do checklist pendente(s).')
    if task.status != 'IN_EXECUTION':
        raise ValueError('A tarefa precisa estar IN_EXECUTION para ser concluída.')
    transition(db, task, actor_id=actor_id, status='COMPLETED', note=note, evidence=evidence)
    row.execution_state='COMPLETED'; row.completed_by=actor_id; row.completed_at=now; row.updated_at=now
    _audit(db, actor_id, 'WORKFLOW_EXECUTION_COMPLETED', task.id, f'evidence={bool(evidence)};note={bool(note)}')
    db.flush(); return row

def sync_execution_states(db: Session, actor_id: int | None = None, now=None):
    now = now or utcnow(); rows = db.query(OperationalWorkflowOrchestration).all(); changed=0
    for row in rows:
        task=db.get(OperationalWorkflowTask,row.task_id)
        if not task: continue
        if task.status == 'COMPLETED': target='COMPLETED'
        elif row.execution_state == 'IN_EXECUTION' or task.status == 'IN_EXECUTION': target='IN_EXECUTION'
        elif row.execution_state == 'ACCEPTED': target='ACCEPTED'
        else: target='PENDING_ACCEPTANCE'
        if target != row.execution_state:
            old=row.execution_state; row.execution_state=target; row.updated_at=now; changed+=1
            if actor_id is not None: _audit(db,actor_id,'WORKFLOW_EXECUTION_STATE_SYNCED',task.id,f'{old}->{target}')
    db.flush(); return {'orchestrations':len(rows),'changed':changed}
