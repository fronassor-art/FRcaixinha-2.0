from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.services.workflow_escalation_v064 import sync_workflow_escalations
from app.models import OperationalActionRecord
router=APIRouter(prefix='/admin/workflow-escalation',tags=['workflow-escalation-v064'])

@router.post('/sync')
def sync(admin=Depends(require_admin), db:Session=Depends(get_db)):
    result=sync_workflow_escalations(db, actor_id=admin.id); db.commit(); return result

@router.get('/summary')
def summary(admin=Depends(require_admin), db:Session=Depends(get_db)):
    result=sync_workflow_escalations(db, actor_id=admin.id); db.commit()
    return {k:result[k] for k in ('open_tasks','escalated','actions_created','actions_updated')}

@router.get('/actions')
def actions(limit:int=Query(100,ge=1,le=500),admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=(db.query(OperationalActionRecord)
          .filter(OperationalActionRecord.action_code=='WORKFLOW_ESCALATION')
          .order_by(OperationalActionRecord.created_at.desc()).limit(limit).all())
    return {'items':[{'id':r.id,'task_id':r.source_task_id,'status':r.status,'assigned_to':r.assigned_to,
                      'escalation_level':r.escalation_level,'note':r.note,
                      'created_at':r.created_at.isoformat(),'updated_at':r.updated_at.isoformat()} for r in rows]}
