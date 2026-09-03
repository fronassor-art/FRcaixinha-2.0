from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import WorkflowIncident
from app.services.workflow_incidents_v071 import sync_incidents, assign_incident, remediate_incident, close_incident, incident_hash
router=APIRouter(prefix="/admin/workflow-incidents",tags=["workflow-incidents-v071"])
class AssignIn(BaseModel): user_id:int
class NoteIn(BaseModel): note:str
class CloseIn(BaseModel): resolution:str
def out(x): return {"id":x.id,"check_code":x.check_code,"severity":x.severity,"status":x.status,"title":x.title,"assigned_to":x.assigned_to,"due_at":x.due_at.isoformat() if x.due_at else None,"remediation_plan":x.remediation_plan,"resolution":x.resolution,"opened_at":x.opened_at.isoformat(),"closed_at":x.closed_at.isoformat() if x.closed_at else None,"integrity_hash":incident_hash(x)}
@router.post("/sync")
def sync(admin=Depends(require_admin),db:Session=Depends(get_db)): r=sync_incidents(db,admin.id); db.commit(); return r
@router.get("")
def list_incidents(status:str|None=None,admin=Depends(require_admin),db:Session=Depends(get_db)):
 q=db.query(WorkflowIncident).order_by(WorkflowIncident.updated_at.desc());
 if status:q=q.filter(WorkflowIncident.status==status.upper())
 return {"items":[out(x) for x in q.limit(500).all()]}
@router.post("/{incident_id}/assign")
def assign(incident_id:int,b:AssignIn,admin=Depends(require_admin),db:Session=Depends(get_db)):
 x=db.get(WorkflowIncident,incident_id)
 if not x:raise HTTPException(404,"Não conformidade não encontrada.")
 assign_incident(db,x,b.user_id,admin.id);db.commit();return out(x)
@router.post("/{incident_id}/remediate")
def remediate(incident_id:int,b:NoteIn,admin=Depends(require_admin),db:Session=Depends(get_db)):
 x=db.get(WorkflowIncident,incident_id)
 if not x:raise HTTPException(404,"Não conformidade não encontrada.")
 try:remediate_incident(db,x,admin.id,b.note);db.commit();return out(x)
 except ValueError as e:db.rollback();raise HTTPException(400,str(e))
@router.post("/{incident_id}/close")
def close(incident_id:int,b:CloseIn,admin=Depends(require_admin),db:Session=Depends(get_db)):
 x=db.get(WorkflowIncident,incident_id)
 if not x:raise HTTPException(404,"Não conformidade não encontrada.")
 try:close_incident(db,x,admin.id,b.resolution);db.commit();return out(x)
 except ValueError as e:db.rollback();raise HTTPException(400,str(e))
