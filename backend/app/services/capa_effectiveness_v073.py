from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from app.models import CorrectiveActionPlan, CorrectiveAction, WorkflowIncident, CapaEffectivenessReview, CapaRecurrenceEvent, AuditLog

def utcnow(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
def review_hash(r):
    return hashlib.sha256(canonical({'id':r.id,'capa_id':r.capa_id,'result':r.result,'score':r.score,'reviewed_by':r.reviewed_by,'notes':r.notes,'reviewed_at':r.reviewed_at.isoformat() if r.reviewed_at else None}).encode()).hexdigest()
def recurrence_hash(e):
    return hashlib.sha256(canonical({'id':e.id,'capa_id':e.capa_id,'incident_id':e.incident_id,'source_check_code':e.source_check_code,'severity':e.severity,'detected_at':e.detected_at.isoformat() if e.detected_at else None,'notes':e.notes}).encode()).hexdigest()

def create_review(db: Session, capa, actor_id: int, result: str, score: int | None = None, notes: str | None = None, now=None):
    if capa.status != 'CLOSED': raise ValueError('A verificação de efetividade recorrente exige CAPA encerrada.')
    if not result.strip(): raise ValueError('Resultado da verificação obrigatório.')
    if score is not None and not 0 <= score <= 100: raise ValueError('Score deve estar entre 0 e 100.')
    now=now or utcnow(); r=CapaEffectivenessReview(capa_id=capa.id,result=result.strip(),score=score,notes=notes,reviewed_by=actor_id,reviewed_at=now,created_at=now)
    db.add(r); db.flush(); db.add(AuditLog(actor_user_id=actor_id,action='CAPA_EFFECTIVENESS_REVIEWED',entity_type='CapaEffectivenessReview',entity_id=str(r.id),details=f'capa={capa.id}'))
    return r

def detect_recurrence(db: Session, capa, incident: WorkflowIncident, actor_id: int | None = None, now=None, notes=None):
    now=now or utcnow()
    existing=db.query(CapaRecurrenceEvent).filter(CapaRecurrenceEvent.capa_id==capa.id, CapaRecurrenceEvent.incident_id==incident.id).first()
    if existing: return existing, False
    e=CapaRecurrenceEvent(capa_id=capa.id,incident_id=incident.id,source_check_code=incident.check_code,severity=incident.severity,detected_at=now,notes=notes,created_at=now)
    db.add(e); db.flush(); db.add(AuditLog(actor_user_id=actor_id,action='CAPA_RECURRENCE_DETECTED',entity_type='CapaRecurrenceEvent',entity_id=str(e.id),details=f'capa={capa.id};incident={incident.id}'))
    return e, True

def reopen_for_recurrence(db: Session, capa, actor_id: int, reason: str, now=None):
    if not reason.strip(): raise ValueError('Motivo da reincidência obrigatório.')
    if capa.status != 'CLOSED': raise ValueError('CAPA precisa estar CLOSED para reabertura por reincidência.')
    now=now or utcnow(); capa.status='REOPENED'; capa.effectiveness_result=None; capa.verified_at=None; capa.closed_at=None; capa.updated_at=now
    db.add(AuditLog(actor_user_id=actor_id,action='CAPA_REOPENED_RECURRENCE',entity_type='CorrectiveActionPlan',entity_id=str(capa.id),details=reason.strip()))
    return capa

def monitor_capa(db: Session, capa, actor_id: int | None = None, now=None):
    now=now or utcnow(); due = capa.due_at and capa.due_at < now and capa.status not in ('CLOSED','REOPENED')
    return {'capa_id':capa.id,'status':capa.status,'overdue':bool(due),'days_overdue':max(0,(now-capa.due_at).days) if due else 0,'reviews':db.query(CapaEffectivenessReview).filter(CapaEffectivenessReview.capa_id==capa.id).count(),'recurrences':db.query(CapaRecurrenceEvent).filter(CapaRecurrenceEvent.capa_id==capa.id).count()}

def sync_capa_recurrence(db: Session, actor_id: int | None = None, now=None):
    now=now or utcnow(); created=reopened=0; items=[]
    capas=db.query(CorrectiveActionPlan).all()
    for capa in capas:
        inc0=db.get(WorkflowIncident,capa.incident_id)
        if not inc0: continue
        incidents=(db.query(WorkflowIncident).filter(WorkflowIncident.check_code==inc0.check_code, WorkflowIncident.id!=inc0.id, WorkflowIncident.opened_at > (capa.closed_at or now), WorkflowIncident.opened_at <= now).order_by(WorkflowIncident.opened_at.asc()).all())
        for inc in incidents:
            ev,is_new=detect_recurrence(db,capa,inc,actor_id,now,notes='Reincidência detectada pelo monitoramento.')
            if is_new:
                created+=1
                if capa.status=='CLOSED':
                    reopen_for_recurrence(db,capa,actor_id or 0,f'Reincidência detectada: incidente {inc.id}',now)
                    reopened+=1
                items.append({'capa_id':capa.id,'incident_id':inc.id,'reopened':capa.status=='REOPENED'})
    db.flush(); return {'recurrences_created':created,'capas_reopened':reopened,'items':items}

def monitor_all(db: Session, now=None):
    now=now or utcnow(); rows=[]
    for c in db.query(CorrectiveActionPlan).order_by(CorrectiveActionPlan.updated_at.desc()).limit(500).all(): rows.append(monitor_capa(db,c,now=now))
    return {'items':rows,'total':len(rows),'overdue':sum(1 for x in rows if x['overdue']),'reopened':sum(1 for x in rows if x['status']=='REOPENED'),'recurrences':sum(x['recurrences'] for x in rows)}
