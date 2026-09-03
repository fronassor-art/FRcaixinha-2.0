from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import WorkflowIncident, CorrectiveActionPlan, CorrectiveAction
from app.services.capa_v072 import create_capa, add_action, complete_action, verify_effectiveness, close_capa, capa_hash
router=APIRouter(prefix='/admin/capa',tags=['capa-v072'])
class CapaIn(BaseModel): objective:str; priority:str='HIGH'; due_at:datetime|None=None; root_cause:str|None=None; effectiveness_criteria:str|None=None
class ActionIn(BaseModel): title:str; description:str|None=None; assigned_to:int|None=None; due_at:datetime|None=None; evidence_required:bool=True
class EvidenceIn(BaseModel): evidence_note:str
class EffectivenessIn(BaseModel): result:str

def out(c,actions): return {'id':c.id,'incident_id':c.incident_id,'status':c.status,'owner_id':c.owner_id,'priority':c.priority,'objective':c.objective,'root_cause':c.root_cause,'effectiveness_criteria':c.effectiveness_criteria,'effectiveness_result':c.effectiveness_result,'due_at':c.due_at.isoformat() if c.due_at else None,'verified_at':c.verified_at.isoformat() if c.verified_at else None,'closed_at':c.closed_at.isoformat() if c.closed_at else None,'actions':[{'id':a.id,'title':a.title,'status':a.status,'assigned_to':a.assigned_to,'due_at':a.due_at.isoformat() if a.due_at else None,'evidence_required':a.evidence_required,'evidence_note':a.evidence_note} for a in actions],'integrity_hash':capa_hash(c,actions)}
@router.post('/incidents/{incident_id}')
def create(incident_id:int,b:CapaIn,admin=Depends(require_admin),db:Session=Depends(get_db)):
    inc=db.get(WorkflowIncident,incident_id)
    if not inc: raise HTTPException(404,'Incidente não encontrado.')
    try: c=create_capa(db,inc,admin.id,b.objective,b.priority,b.due_at,b.root_cause,b.effectiveness_criteria); db.commit(); return out(c,db.query(CorrectiveAction).filter(CorrectiveAction.capa_id==c.id).all())
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
@router.post('/{capa_id}/actions')
def action(capa_id:int,b:ActionIn,admin=Depends(require_admin),db:Session=Depends(get_db)):
    c=db.get(CorrectiveActionPlan,capa_id)
    if not c: raise HTTPException(404,'CAPA não encontrada.')
    try: a=add_action(db,c,admin.id,b.title,b.description,b.assigned_to,b.due_at,b.evidence_required); db.commit(); return out(c,db.query(CorrectiveAction).filter(CorrectiveAction.capa_id==c.id).all())
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
@router.post('/actions/{action_id}/complete')
def complete(action_id:int,b:EvidenceIn,admin=Depends(require_admin),db:Session=Depends(get_db)):
    a=db.get(CorrectiveAction,action_id)
    if not a: raise HTTPException(404,'Ação não encontrada.')
    try: complete_action(db,a,admin.id,b.evidence_note); db.commit(); c=db.get(CorrectiveActionPlan,a.capa_id); return out(c,db.query(CorrectiveAction).filter(CorrectiveAction.capa_id==c.id).all())
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
@router.post('/{capa_id}/verify-effectiveness')
def verify(capa_id:int,b:EffectivenessIn,admin=Depends(require_admin),db:Session=Depends(get_db)):
    c=db.get(CorrectiveActionPlan,capa_id)
    if not c: raise HTTPException(404,'CAPA não encontrada.')
    try: verify_effectiveness(db,c,admin.id,b.result); db.commit(); return out(c,db.query(CorrectiveAction).filter(CorrectiveAction.capa_id==c.id).all())
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
@router.post('/{capa_id}/close')
def close(capa_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    c=db.get(CorrectiveActionPlan,capa_id)
    if not c: raise HTTPException(404,'CAPA não encontrada.')
    try: close_capa(db,c,admin.id); db.commit(); return out(c,db.query(CorrectiveAction).filter(CorrectiveAction.capa_id==c.id).all())
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
@router.get('/{capa_id}')
def get(capa_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    c=db.get(CorrectiveActionPlan,capa_id)
    if not c: raise HTTPException(404,'CAPA não encontrada.')
    return out(c,db.query(CorrectiveAction).filter(CorrectiveAction.capa_id==c.id).all())
@router.get('')
def listing(status:str|None=None,admin=Depends(require_admin),db:Session=Depends(get_db)):
    q=db.query(CorrectiveActionPlan).order_by(CorrectiveActionPlan.updated_at.desc())
    if status:q=q.filter(CorrectiveActionPlan.status==status.upper())
    return {'items':[out(c,db.query(CorrectiveAction).filter(CorrectiveAction.capa_id==c.id).all()) for c in q.limit(500).all()]}
