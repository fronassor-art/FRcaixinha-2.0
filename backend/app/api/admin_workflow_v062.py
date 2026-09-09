from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import OperationalWorkflowTask, OperationalWorkflowEvent
from app.services.admin_workflow_v062 import create_task, transition
router=APIRouter(prefix='/admin/workflow',tags=['workflow-v062'])
class CreateIn(BaseModel):
    action_code:str; priority:str='MEDIUM'; assigned_to:int|None=None; due_at:str|None=None; description:str|None=None
class TransitionIn(BaseModel):
    status:str; note:str|None=None; evidence:str|None=None; assigned_to:int|None=None
@router.post('/tasks')
def create(body:CreateIn,admin=Depends(require_admin),db:Session=Depends(get_db)):
    from datetime import datetime
    try:
        due=datetime.fromisoformat(body.due_at) if body.due_at else None
        t=create_task(db,action_code=body.action_code.upper(),actor_id=admin.id,priority=body.priority.upper(),assigned_to=body.assigned_to,due_at=due,description=body.description); db.commit(); db.refresh(t)
        return {'id':t.id,'action_code':t.action_code,'status':t.status,'priority':t.priority,'assigned_to':t.assigned_to,'due_at':t.due_at.isoformat() if t.due_at else None}
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
@router.post('/tasks/{task_id}/transition')
def change(task_id:int,body:TransitionIn,admin=Depends(require_admin),db:Session=Depends(get_db)):
    t=db.get(OperationalWorkflowTask,task_id)
    if not t: raise HTTPException(404,'Tarefa não encontrada.')
    try: transition(db,t,actor_id=admin.id,status=body.status,note=body.note,evidence=body.evidence,assigned_to=body.assigned_to); db.commit(); db.refresh(t); return {'id':t.id,'status':t.status,'assigned_to':t.assigned_to}
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
@router.get('/tasks')
def list_tasks(status:str|None=None,priority:str|None=None,limit:int=Query(100,ge=1,le=500),admin=Depends(require_admin),db:Session=Depends(get_db)):
    q=db.query(OperationalWorkflowTask).order_by(OperationalWorkflowTask.created_at.desc())
    if status:q=q.filter(OperationalWorkflowTask.status==status.upper())
    if priority:q=q.filter(OperationalWorkflowTask.priority==priority.upper())
    rows=q.limit(limit).all(); return {'items':[{'id':t.id,'action_code':t.action_code,'status':t.status,'priority':t.priority,'assigned_to':t.assigned_to,'due_at':t.due_at.isoformat() if t.due_at else None,'description':t.description} for t in rows]}
@router.get('/tasks/{task_id}')
def detail(task_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    t=db.get(OperationalWorkflowTask,task_id)
    if not t: raise HTTPException(404,'Tarefa não encontrada.')
    events=db.query(OperationalWorkflowEvent).filter_by(task_id=task_id).order_by(OperationalWorkflowEvent.id).all()
    return {'task':{'id':t.id,'action_code':t.action_code,'status':t.status,'priority':t.priority,'assigned_to':t.assigned_to,'due_at':t.due_at.isoformat() if t.due_at else None,'description':t.description},'events':[{'id':e.id,'actor_id':e.actor_id,'from_status':e.from_status,'to_status':e.to_status,'note':e.note,'evidence':e.evidence,'event_hash':e.event_hash,'created_at':e.created_at.isoformat()} for e in events]}
