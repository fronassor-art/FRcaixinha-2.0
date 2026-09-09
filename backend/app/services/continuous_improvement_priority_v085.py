from __future__ import annotations
import hashlib,json
from datetime import datetime,timezone
from sqlalchemy.orm import Session
from app.models import ContinuousImprovementRecommendation, ContinuousImprovementPlan, ContinuousImprovementMeasurement, OperationalRiskTrendSnapshot, ContinuousImprovementPrioritySnapshot, AuditLog

def now(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,default=str)
def digest(v): return hashlib.sha256(canonical(v).encode()).hexdigest()

def _score(r, plans, measurements, risk):
    # deterministic, explainable 0-100: risk 0-30, impact/pattern 0-20, urgency 0-20, recurrence 0-15, ineffectiveness 0-10, SLA 0-5
    risk_score=min(30,int((risk or 0)*0.30))
    pattern_score={'INEFFECTIVE_PATTERN':20,'NEGATIVE_DELTA_PATTERN':17,'PARTIAL_PATTERN':12}.get(r.pattern_code,5)
    rp=[p for p in plans if p.recommendation_id==r.id]
    openp=[p for p in rp if p.status not in ('CLOSED',)]
    overdue=sum(1 for p in openp if p.due_at and p.due_at<now())
    urgency=20 if overdue else (14 if openp else 8)
    ms=[m for p in rp for m in measurements if m.plan_id==p.id]
    ineffective=sum(m.result=='INEFFECTIVE' for m in ms)
    recurrence=min(15, max(0,(r.sample_size-1)*3))
    ineffect=min(10, ineffective*3)
    sla=5 if overdue else (2 if openp else 0)
    score=min(100,risk_score+pattern_score+urgency+recurrence+ineffect+sla)
    priority='CRITICAL' if score>=75 else ('HIGH' if score>=55 else ('MEDIUM' if score>=30 else 'LOW'))
    reasons=[]
    if risk_score: reasons.append(f'risco={risk_score}/30')
    if pattern_score>=17: reasons.append(f'padrao={r.pattern_code}')
    if overdue: reasons.append('SLA_atrasado')
    if ineffective: reasons.append(f'inefetividade={ineffective}')
    if recurrence: reasons.append(f'amostra={r.sample_size}')
    return score,priority,{'risk':risk_score,'impact':pattern_score,'urgency':urgency,'recurrence':recurrence,'effectiveness':ineffect,'sla':sla,'reasons':reasons}

def build_queue(db:Session):
    risk=db.query(OperationalRiskTrendSnapshot).order_by(OperationalRiskTrendSnapshot.snapshot_date.desc()).first()
    risk_score=risk.risk_score if risk else 0
    recs=db.query(ContinuousImprovementRecommendation).filter(ContinuousImprovementRecommendation.status.in_(['OPEN','ACCEPTED','IMPLEMENTED'])).all()
    plans=db.query(ContinuousImprovementPlan).all(); measurements=db.query(ContinuousImprovementMeasurement).all()
    items=[]
    for r in recs:
        score,priority,breakdown=_score(r,plans,measurements,risk_score)
        items.append({'recommendation_id':r.id,'indicator_code':r.indicator_code,'pattern_code':r.pattern_code,'status':r.status,'priority_score':score,'priority':priority,'breakdown':breakdown})
    items.sort(key=lambda x:(-x['priority_score'],x['recommendation_id']))
    return {'schema':'v0.85','generated_at':now().isoformat(),'risk_score':risk_score,'items':items,'counts':{p:sum(x['priority']==p for x in items) for p in ['CRITICAL','HIGH','MEDIUM','LOW']}}

def persist(db:Session,actor_id:int|None=None):
    data=build_queue(db); h=digest(data); today=now().date()
    row=db.query(ContinuousImprovementPrioritySnapshot).filter_by(snapshot_date=today).first()
    if row: row.status='CRITICAL' if data['counts']['CRITICAL'] else ('ATTENTION' if data['counts']['HIGH'] else 'PASS'); row.snapshot_json=canonical(data); row.snapshot_hash=h; row.generated_by=actor_id; row.updated_at=now()
    else:
        row=ContinuousImprovementPrioritySnapshot(snapshot_date=today,status='CRITICAL' if data['counts']['CRITICAL'] else ('ATTENTION' if data['counts']['HIGH'] else 'PASS'),snapshot_json=canonical(data),snapshot_hash=h,generated_by=actor_id,created_at=now(),updated_at=now()); db.add(row)
    db.flush(); return row,data
