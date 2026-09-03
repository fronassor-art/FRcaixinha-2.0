from __future__ import annotations
import hashlib, json
from datetime import date, datetime, timezone, timedelta
from sqlalchemy.orm import Session
from app.models import (OperationalRiskTrendSnapshot, WorkflowIncident, CapaRecurrenceEvent,
                        CorrectiveActionPlan, CapaEffectivenessReview, AuditLog)

def utcnow(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
def snapshot_hash(payload): return hashlib.sha256(canonical(payload).encode()).hexdigest()

def _window_counts(db, start, end):
    incidents=db.query(WorkflowIncident).filter(WorkflowIncident.opened_at >= start, WorkflowIncident.opened_at < end).all()
    recurrences=db.query(CapaRecurrenceEvent).filter(CapaRecurrenceEvent.detected_at >= start, CapaRecurrenceEvent.detected_at < end).all()
    reviews=db.query(CapaEffectivenessReview).filter(CapaEffectivenessReview.reviewed_at >= start, CapaEffectivenessReview.reviewed_at < end).all()
    ineffective=sum(1 for r in reviews if r.score is not None and r.score < 70)
    critical=sum(1 for i in incidents if i.severity == 'CRITICAL')
    return {'incidents':len(incidents),'critical_incidents':critical,'recurrences':len(recurrences),'ineffective_reviews':ineffective}

def calculate_risk(db: Session, now=None):
    now=now or utcnow(); start=now-timedelta(days=30); prev_start=start-timedelta(days=30)
    cur=_window_counts(db,start,now); prev=_window_counts(db,prev_start,start)
    open_capas=db.query(CorrectiveActionPlan).filter(CorrectiveActionPlan.status != 'CLOSED').all()
    overdue=sum(1 for c in open_capas if c.due_at and c.due_at < now)
    reopened=sum(1 for c in db.query(CorrectiveActionPlan).filter(CorrectiveActionPlan.status=='REOPENED').all())
    trend=[]
    for k in cur:
        delta=cur[k]-prev[k]; trend.append({'metric':k,'current':cur[k],'previous':prev[k],'delta':delta,'direction':'UP' if delta>0 else 'DOWN' if delta<0 else 'STABLE'})
    score=min(100, cur['critical_incidents']*20 + cur['recurrences']*15 + cur['ineffective_reviews']*10 + overdue*8 + reopened*10)
    # A rising incident/reoccurrence pattern is a warning even when absolute volume is low.
    rising=sum(1 for x in trend if x['direction']=='UP' and x['metric'] in ('critical_incidents','recurrences','ineffective_reviews'))
    score=min(100,score + rising*5)
    status='CRITICAL' if score>=70 else 'ATTENTION' if score>=30 else 'PASS'
    return {'status':status,'risk_score':score,'window_days':30,'current':cur,'previous':prev,
            'open_capa':len(open_capas),'overdue_capa':overdue,'reopened_capa':reopened,'trends':trend,
            'method':'DETERMINISTIC_V074','generated_at':now.isoformat()}

def persist_risk_snapshot(db: Session, generated_by=None, snapshot_date=None, now=None):
    now=now or utcnow(); snapshot_date=snapshot_date or now.date()
    data=calculate_risk(db,now); payload={'snapshot_date':snapshot_date.isoformat(),**data}; h=snapshot_hash(payload)
    row=db.query(OperationalRiskTrendSnapshot).filter_by(snapshot_date=snapshot_date).first()
    if row:
        row.status=data['status']; row.risk_score=data['risk_score']; row.snapshot_json=canonical(payload); row.snapshot_hash=h; row.generated_by=generated_by; return row,data
    row=OperationalRiskTrendSnapshot(snapshot_date=snapshot_date,status=data['status'],risk_score=data['risk_score'],snapshot_json=canonical(payload),snapshot_hash=h,generated_by=generated_by,created_at=now)
    db.add(row); db.flush(); db.add(AuditLog(actor_user_id=generated_by,action='OPERATIONAL_RISK_SNAPSHOT_CREATED',entity_type='OperationalRiskTrendSnapshot',entity_id=str(row.id),details=f'status={data["status"]};score={data["risk_score"]}'))
    return row,data
