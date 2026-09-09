from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import CorrectiveActionPlan, CorrectiveAction, WorkflowIncident, AuditLog

def utcnow(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
def capa_hash(capa, actions):
    payload={'id':capa.id,'incident_id':capa.incident_id,'status':capa.status,'owner_id':capa.owner_id,'priority':capa.priority,'objective':capa.objective,'root_cause':capa.root_cause,'effectiveness_criteria':capa.effectiveness_criteria,'effectiveness_result':capa.effectiveness_result,'actions':[{'id':a.id,'title':a.title,'status':a.status,'assigned_to':a.assigned_to,'due_at':a.due_at.isoformat() if a.due_at else None,'evidence_note':a.evidence_note} for a in sorted(actions,key=lambda x:x.id)]}
    return hashlib.sha256(canonical(payload).encode()).hexdigest()

def create_capa(db:Session, incident:WorkflowIncident, actor_id:int, objective:str, priority='HIGH', due_at=None, root_cause=None, effectiveness_criteria=None):
    if not objective.strip(): raise ValueError('Objetivo obrigatório.')
    existing=db.query(CorrectiveActionPlan).filter(CorrectiveActionPlan.incident_id==incident.id).first()
    if existing: return existing
    now=utcnow(); capa=CorrectiveActionPlan(incident_id=incident.id,status='OPEN',priority=priority,objective=objective.strip(),due_at=due_at,root_cause=root_cause,effectiveness_criteria=effectiveness_criteria,created_at=now,updated_at=now)
    db.add(capa); db.flush(); db.add(AuditLog(actor_user_id=actor_id,action='CAPA_CREATED',entity_type='CorrectiveActionPlan',entity_id=str(capa.id),details=f'incident={incident.id}')); return capa

def add_action(db,capa,actor_id,title,description=None,assigned_to=None,due_at=None,evidence_required=True):
    if not title.strip(): raise ValueError('Título da ação obrigatório.')
    now=utcnow(); a=CorrectiveAction(capa_id=capa.id,title=title.strip(),description=description,assigned_to=assigned_to,due_at=due_at,evidence_required=evidence_required,created_at=now,updated_at=now)
    db.add(a); capa.status='IN_EXECUTION'; capa.updated_at=now; db.flush(); db.add(AuditLog(actor_user_id=actor_id,action='CAPA_ACTION_CREATED',entity_type='CorrectiveAction',entity_id=str(a.id),details=f'capa={capa.id}')); return a

def complete_action(db,a,actor_id,evidence_note=None):
    if a.status=='COMPLETED': raise ValueError('Ação já concluída.')
    if a.evidence_required and not evidence_note: raise ValueError('Evidência obrigatória para concluir a ação.')
    now=utcnow(); a.status='COMPLETED'; a.evidence_note=evidence_note; a.completed_at=now; a.updated_at=now
    db.add(AuditLog(actor_user_id=actor_id,action='CAPA_ACTION_COMPLETED',entity_type='CorrectiveAction',entity_id=str(a.id),details='ação concluída com evidência'))

def verify_effectiveness(db,capa,actor_id,result):
    if not result.strip(): raise ValueError('Resultado de eficácia obrigatório.')
    actions=db.query(CorrectiveAction).filter(CorrectiveAction.capa_id==capa.id).all()
    if not actions or any(a.status!='COMPLETED' for a in actions): raise ValueError('Todas as ações precisam estar concluídas antes da verificação de eficácia.')
    now=utcnow(); capa.effectiveness_result=result.strip(); capa.status='EFFECTIVENESS_VERIFIED'; capa.verified_at=now; capa.updated_at=now
    db.add(AuditLog(actor_user_id=actor_id,action='CAPA_EFFECTIVENESS_VERIFIED',entity_type='CorrectiveActionPlan',entity_id=str(capa.id),details='eficácia verificada'))

def close_capa(db,capa,actor_id):
    if capa.status!='EFFECTIVENESS_VERIFIED': raise ValueError('CAPA só pode ser encerrada após verificação de eficácia.')
    now=utcnow(); capa.status='CLOSED'; capa.closed_at=now; capa.updated_at=now
    db.add(AuditLog(actor_user_id=actor_id,action='CAPA_CLOSED',entity_type='CorrectiveActionPlan',entity_id=str(capa.id),details='plano encerrado'))
