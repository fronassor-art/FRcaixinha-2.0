from __future__ import annotations
import hashlib, json
from datetime import date, datetime, timezone
from sqlalchemy.orm import Session
from app.models import (ContinuousImprovementRecommendation, ContinuousImprovementPlan,
    ContinuousImprovementMeasurement, OperationalRiskTrendSnapshot)

def now(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
def digest(v): return hashlib.sha256(canonical(v).encode()).hexdigest()

def build_dashboard(db: Session, today: date | None = None):
    today = today or date.today()
    risk = db.query(OperationalRiskTrendSnapshot).order_by(OperationalRiskTrendSnapshot.snapshot_date.desc()).first()
    recs = db.query(ContinuousImprovementRecommendation).all()
    plans = db.query(ContinuousImprovementPlan).all()
    measurements = db.query(ContinuousImprovementMeasurement).all()
    active_recs = [r for r in recs if r.status in ('OPEN','ACCEPTED')]
    accepted = [r for r in recs if r.status == 'ACCEPTED']
    rejected = [r for r in recs if r.status == 'REJECTED']
    deferred = [r for r in recs if r.status == 'DEFERRED']
    open_plans = [p for p in plans if p.status not in ('CLOSED',)]
    implemented = [p for p in plans if p.status in ('IMPLEMENTED','MEASURED','CLOSED','REVIEW_REQUIRED')]
    overdue = [p for p in open_plans if p.due_at and p.due_at < now()]
    unassigned = [p for p in open_plans if not p.assigned_to]
    effective = [m for m in measurements if m.result == 'EFFECTIVE']
    ineffective = [m for m in measurements if m.result == 'INEFFECTIVE']
    partial = [m for m in measurements if m.result == 'PARTIAL']
    verified = [m for m in measurements if m.verified_at is not None]
    total_measured = len([m for m in measurements if m.result in ('EFFECTIVE','INEFFECTIVE','PARTIAL')])
    effectiveness_rate = (len(effective) / total_measured * 100) if total_measured else 0.0
    indicator = {}
    for m in measurements:
        item = indicator.setdefault(m.plan_id, {'count':0,'effective':0,'ineffective':0,'partial':0,'delta_sum':0.0})
        item['count'] += 1
        item[m.result.lower()] = item.get(m.result.lower(), 0) + 1
        if m.delta is not None: item['delta_sum'] += m.delta
    rec_by_pattern = {}
    for r in recs: rec_by_pattern[r.pattern_code] = rec_by_pattern.get(r.pattern_code, 0) + 1
    risk_data = json.loads(risk.snapshot_json) if risk else {'risk_score':0,'status':'PASS','trends':[]}
    flags=[]
    if ineffective: flags.append('INEFFECTIVE_IMPROVEMENTS')
    if overdue: flags.append('IMPROVEMENT_PLANS_OVERDUE')
    if unassigned: flags.append('UNASSIGNED_IMPROVEMENT_PLANS')
    if accepted: flags.append('ACCEPTED_RECOMMENDATIONS')
    if risk_data.get('risk_score',0) >= 70: flags.append('OPERATIONAL_RISK_CRITICAL')
    status = 'CRITICAL' if 'OPERATIONAL_RISK_CRITICAL' in flags or len(ineffective) > len(effective) and total_measured else ('ATTENTION' if flags else 'PASS')
    return {'schema':'v0.84','snapshot_date':today.isoformat(),'status':status,
      'risk':{'score':risk_data.get('risk_score',0),'status':risk_data.get('status','PASS'),'snapshot_id':risk.id if risk else None,'trends':risk_data.get('trends',[])},
      'recommendations':{'total':len(recs),'active':len(active_recs),'accepted':len(accepted),'rejected':len(rejected),'deferred':len(deferred),'by_pattern':rec_by_pattern},
      'plans':{'total':len(plans),'open':len(open_plans),'implemented':len(implemented),'overdue':len(overdue),'unassigned':len(unassigned),'review_required':sum(1 for p in plans if p.status=='REVIEW_REQUIRED')},
      'measurements':{'total':len(measurements),'measured':total_measured,'effective':len(effective),'partial':len(partial),'ineffective':len(ineffective),'verified':len(verified),'effectiveness_rate':round(effectiveness_rate,2)},
      'indicators': [{'plan_id':pid, **vals, 'avg_delta': round(vals['delta_sum']/vals['count'],6) if vals['count'] else 0} for pid,vals in indicator.items()],
      'flags':flags}

def persist_dashboard(db: Session, generated_by: int | None = None, today: date | None = None):
    from app.models import ContinuousImprovementDashboardSnapshot
    data=build_dashboard(db,today); raw=canonical(data); h=digest(data)
    d=today or date.today()
    row=db.query(ContinuousImprovementDashboardSnapshot).filter_by(snapshot_date=d).first()
    if row:
        row.status=data['status']; row.snapshot_json=raw; row.snapshot_hash=h; row.generated_by=generated_by; row.updated_at=now()
    else:
        row=ContinuousImprovementDashboardSnapshot(snapshot_date=d,status=data['status'],snapshot_json=raw,snapshot_hash=h,generated_by=generated_by,created_at=now(),updated_at=now()); db.add(row)
    db.flush(); return row,data
