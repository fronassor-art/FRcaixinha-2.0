from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import OperationalWorkflowTask, WorkflowExecutionChecklistItem
from app.services.workflow_evidence_v067 import add_evidence, add_checklist_item, complete_checklist_item, checklist_status, evidence_summary
router=APIRouter(prefix='/admin/workflow-evidence',tags=['workflow-evidence-v067'])
class EvidenceIn(BaseModel):
    evidence_type:str='NOTE'; title:str|None=None; content:str=Field(min_length=1)
class ChecklistIn(BaseModel):
    label:str=Field(min_length=1); required:bool=True

def task_or_404(db, task_id):
    t=db.get(OperationalWorkflowTask,task_id)
    if not t: raise HTTPException(404,'Tarefa não encontrada.')
    return t

@router.post('/tasks/{task_id}/evidence')
def evidence(task_id:int, body:EvidenceIn, admin=Depends(require_admin), db:Session=Depends(get_db)):
    try: r=add_evidence(db,task_or_404(db,task_id),admin.id,body.evidence_type,body.content,body.title); db.commit(); return {'id':r.id,'task_id':r.task_id,'type':r.evidence_type,'content_hash':r.content_hash}
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))

@router.post('/tasks/{task_id}/checklist')
def checklist(task_id:int, body:ChecklistIn, admin=Depends(require_admin), db:Session=Depends(get_db)):
    try: r=add_checklist_item(db,task_or_404(db,task_id),admin.id,body.label,body.required); db.commit(); return {'id':r.id,'task_id':r.task_id,'label':r.label,'required':r.required,'completed':r.completed}
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))

@router.post('/checklist/{item_id}/complete')
def checklist_complete(item_id:int, admin=Depends(require_admin), db:Session=Depends(get_db)):
    item=db.get(WorkflowExecutionChecklistItem,item_id)
    if not item: raise HTTPException(404,'Item de checklist não encontrado.')
    try: r=complete_checklist_item(db,item,admin.id); db.commit(); return {'id':r.id,'completed':r.completed,'completed_by':r.completed_by,'completed_at':r.completed_at.isoformat() if r.completed_at else None}
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))

@router.get('/tasks/{task_id}/summary')
def summary(task_id:int, admin=Depends(require_admin), db:Session=Depends(get_db)):
    task_or_404(db,task_id)
    return {'task_id':task_id,'checklist':checklist_status(db,task_id),'evidence':evidence_summary(db,task_id)}
