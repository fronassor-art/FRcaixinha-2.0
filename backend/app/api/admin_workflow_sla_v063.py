from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import OperationalWorkflowTask
from app.services.workflow_sla_v063 import enrich_task_sla, apply_sla
router=APIRouter(prefix='/admin/workflow-sla',tags=['workflow-sla-v063'])

def dto(t):
    x=enrich_task_sla(t); return {'id':t.id,'action_code':t.action_code,'status':t.status,'priority':t.priority,'assigned_to':t.assigned_to,'sla_status':t.sla_status,'escalation_level':t.escalation_level,'escalated_at':t.escalated_at.isoformat() if t.escalated_at else None,'sla':x}

@router.get('/summary')
def summary(admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=db.query(OperationalWorkflowTask).filter(OperationalWorkflowTask.status!='COMPLETED').all()
    for t in rows: apply_sla(db,t,actor_id=admin.id)
    db.commit()
    return {'total_open':len(rows),'overdue':sum(1 for t in rows if t.sla_status=='OVERDUE'),'critical_overdue':sum(1 for t in rows if t.sla_status=='OVERDUE' and t.priority=='CRITICAL')}

@router.get('/tasks')
def tasks(overdue:bool=False,limit:int=Query(100,ge=1,le=500),admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=db.query(OperationalWorkflowTask).order_by(OperationalWorkflowTask.due_at.asc().nulls_last()).limit(limit).all()
    out=[]
    for t in rows:
        info=apply_sla(db,t,actor_id=admin.id)
        if not overdue or info['overdue']: out.append(dto(t))
    db.commit(); return {'items':out}
