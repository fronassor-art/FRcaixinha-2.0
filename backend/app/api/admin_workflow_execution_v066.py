from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import OperationalWorkflowTask, OperationalWorkflowOrchestration
from app.services.workflow_execution_v066 import accept, start, complete, sync_execution_states

router=APIRouter(prefix='/admin/workflow-execution',tags=['workflow-execution-v066'])
class CompleteIn(BaseModel):
    note:str|None=None
    evidence:str|None=None

def _result(row):
    return {'orchestration_id':row.id,'task_id':row.task_id,'execution_state':row.execution_state,'accepted_by':row.accepted_by,'accepted_at':row.accepted_at.isoformat() if row.accepted_at else None,'started_by':row.started_by,'started_at':row.started_at.isoformat() if row.started_at else None,'completed_by':row.completed_by,'completed_at':row.completed_at.isoformat() if row.completed_at else None}

@router.post('/sync')
def sync(admin=Depends(require_admin),db:Session=Depends(get_db)):
    r=sync_execution_states(db,actor_id=admin.id); db.commit(); return r

@router.post('/tasks/{task_id}/accept')
def accept_task(task_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    t=db.get(OperationalWorkflowTask,task_id)
    if not t: raise HTTPException(404,'Tarefa não encontrada.')
    try: r=accept(db,t,admin.id); db.commit(); return _result(r)
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))

@router.post('/tasks/{task_id}/start')
def start_task(task_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    t=db.get(OperationalWorkflowTask,task_id)
    if not t: raise HTTPException(404,'Tarefa não encontrada.')
    try: r=start(db,t,admin.id); db.commit(); return _result(r)
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))

@router.post('/tasks/{task_id}/complete')
def complete_task(task_id:int,body:CompleteIn,admin=Depends(require_admin),db:Session=Depends(get_db)):
    t=db.get(OperationalWorkflowTask,task_id)
    if not t: raise HTTPException(404,'Tarefa não encontrada.')
    try: r=complete(db,t,admin.id,body.note,body.evidence); db.commit(); return _result(r)
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))

@router.get('/queue')
def queue(state:str|None=None,admin=Depends(require_admin),db:Session=Depends(get_db)):
    q=db.query(OperationalWorkflowOrchestration).order_by(OperationalWorkflowOrchestration.orchestration_score.desc(),OperationalWorkflowOrchestration.updated_at.asc())
    if state: q=q.filter(OperationalWorkflowOrchestration.execution_state==state.upper())
    return {'items':[_result(r) for r in q.limit(500).all()]}
