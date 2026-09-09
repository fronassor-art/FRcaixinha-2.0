from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import OperationalWorkflowTask, OperationalWorkflowOrchestration, WorkflowExecutionEvidence, WorkflowExecutionChecklistItem, AuditLog

def utcnow(): return datetime.now(timezone.utc)

def _audit(db, actor_id, action, task_id, details):
    db.add(AuditLog(actor_user_id=actor_id, action=action, entity_type='OperationalWorkflowTask', entity_id=str(task_id), details=details))

def add_evidence(db: Session, task: OperationalWorkflowTask, actor_id: int, evidence_type: str, content: str, title: str|None=None):
    orch=db.query(OperationalWorkflowOrchestration).filter_by(task_id=task.id).first()
    if not orch or orch.execution_state != 'IN_EXECUTION' or orch.started_by != actor_id:
        raise ValueError('Somente o administrador que iniciou a execução pode adicionar evidência.')
    content=(content or '').strip()
    if not content: raise ValueError('A evidência não pode ser vazia.')
    et=(evidence_type or 'NOTE').upper()
    if et not in {'NOTE','LINK','ATTACHMENT'}: raise ValueError('Tipo de evidência inválido.')
    h=hashlib.sha256(content.encode()).hexdigest()
    row=WorkflowExecutionEvidence(task_id=task.id,added_by=actor_id,evidence_type=et,title=title,content=content,content_hash=h,created_at=utcnow())
    db.add(row); _audit(db,actor_id,'WORKFLOW_EVIDENCE_ADDED',task.id,f'type={et};hash={h}')
    db.flush(); return row

def add_checklist_item(db: Session, task: OperationalWorkflowTask, actor_id: int, label: str, required: bool=True):
    orch=db.query(OperationalWorkflowOrchestration).filter_by(task_id=task.id).first()
    if not orch or orch.execution_state != 'IN_EXECUTION' or orch.started_by != actor_id: raise ValueError('A tarefa precisa estar em execução pelo administrador responsável.')
    label=(label or '').strip()
    if not label: raise ValueError('O item do checklist não pode ser vazio.')
    row=WorkflowExecutionChecklistItem(task_id=task.id,label=label,required=required,created_at=utcnow())
    db.add(row); _audit(db,actor_id,'WORKFLOW_CHECKLIST_ITEM_ADDED',task.id,f'required={required};label={label[:120]}')
    db.flush(); return row

def complete_checklist_item(db: Session, item: WorkflowExecutionChecklistItem, actor_id: int):
    orch=db.query(OperationalWorkflowOrchestration).filter_by(task_id=item.task_id).first()
    if not orch or orch.execution_state != 'IN_EXECUTION' or orch.started_by != actor_id: raise ValueError('Somente o administrador responsável pode concluir o checklist.')
    if item.completed: return item
    item.completed=True; item.completed_by=actor_id; item.completed_at=utcnow()
    _audit(db,actor_id,'WORKFLOW_CHECKLIST_ITEM_COMPLETED',item.task_id,f'item_id={item.id}')
    db.flush(); return item

def checklist_status(db: Session, task_id: int):
    rows=db.query(WorkflowExecutionChecklistItem).filter_by(task_id=task_id).order_by(WorkflowExecutionChecklistItem.id).all()
    required=[r for r in rows if r.required]
    return {'items':len(rows),'required':len(required),'completed':sum(r.completed for r in rows),'required_pending':sum(not r.completed for r in required),'ready':all(r.completed for r in required)}

def evidence_summary(db: Session, task_id: int):
    rows=db.query(WorkflowExecutionEvidence).filter_by(task_id=task_id).order_by(WorkflowExecutionEvidence.created_at.asc()).all()
    return [{'id':r.id,'type':r.evidence_type,'title':r.title,'content':r.content,'content_hash':r.content_hash,'added_by':r.added_by,'created_at':r.created_at.isoformat()} for r in rows]
