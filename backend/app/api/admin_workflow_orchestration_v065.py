from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import OperationalWorkflowOrchestration
from app.services.workflow_orchestration_v065 import sync_workflow_orchestration

router=APIRouter(prefix='/admin/workflow-orchestration',tags=['workflow-orchestration-v065'])

@router.post('/sync')
def sync(admin=Depends(require_admin), db:Session=Depends(get_db)):
    result=sync_workflow_orchestration(db, actor_id=admin.id); db.commit(); return result

@router.get('/summary')
def summary(admin=Depends(require_admin), db:Session=Depends(get_db)):
    result=sync_workflow_orchestration(db, actor_id=admin.id); db.commit()
    rows=db.query(OperationalWorkflowOrchestration).all()
    by_queue={k:sum(1 for r in rows if r.queue_status==k) for k in ('READY','ASSIGNED','IN_PROGRESS','ESCALATED','COMPLETED')}
    by_priority={k:sum(1 for r in rows if r.priority==k) for k in ('LOW','MEDIUM','HIGH','CRITICAL')}
    return {'tasks':len(rows),'queue':by_queue,'priority':by_priority,'escalated':sum(1 for r in rows if r.escalation_level!='NONE')}

@router.get('/queue')
def queue(status:str|None=None, assigned_to:int|None=None, limit:int=Query(100,ge=1,le=500), admin=Depends(require_admin), db:Session=Depends(get_db)):
    q=db.query(OperationalWorkflowOrchestration).order_by(OperationalWorkflowOrchestration.orchestration_score.desc(), OperationalWorkflowOrchestration.updated_at.asc())
    if status: q=q.filter(OperationalWorkflowOrchestration.queue_status==status.upper())
    if assigned_to is not None: q=q.filter(OperationalWorkflowOrchestration.assigned_to==assigned_to)
    rows=q.limit(limit).all()
    return {'items':[{'id':r.id,'task_id':r.task_id,'queue_status':r.queue_status,'assigned_to':r.assigned_to,
      'priority':r.priority,'sla_status':r.sla_status,'escalation_level':r.escalation_level,'execution_state':getattr(r,'execution_state','PENDING_ACCEPTANCE'),
      'orchestration_score':r.orchestration_score,'updated_at':r.updated_at.isoformat()} for r in rows]}
