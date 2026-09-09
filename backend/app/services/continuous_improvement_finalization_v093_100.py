from __future__ import annotations
import csv, hashlib, io, json
from datetime import date, datetime, timezone
from sqlalchemy.orm import Session
from app.models import (User, ContinuousImprovementRecommendation, ContinuousImprovementPlan,
    ContinuousImprovementAssignmentDecision, ContinuousImprovementExecution,
    ContinuousImprovementCertification, ContinuousImprovementExecutiveAuditSnapshot,
    ContinuousImprovementDashboardExecutiveSnapshot, ContinuousImprovementActionQueueSnapshot,
    ContinuousImprovementKpiSnapshot, ContinuousImprovementSlaSnapshot,
    ContinuousImprovementComplianceSnapshot, ContinuousImprovementExportSnapshot,
    ContinuousImprovementProductionReadinessSnapshot, ContinuousImprovementProgramReleaseSnapshot,
    AuditLog)

def now(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
def digest(v): return hashlib.sha256(canonical(v).encode()).hexdigest()

def _counts(items, attr='status'):
    out={}
    for x in items:
        k=getattr(x,attr,None); out[k]=out.get(k,0)+1
    return out

def build_dashboard(db: Session):
    recs=db.query(ContinuousImprovementRecommendation).all(); plans=db.query(ContinuousImprovementPlan).all()
    ex=db.query(ContinuousImprovementExecution).all(); certs=db.query(ContinuousImprovementCertification).all()
    report=db.query(ContinuousImprovementExecutiveAuditSnapshot).order_by(ContinuousImprovementExecutiveAuditSnapshot.id.desc()).first()
    overdue=sum(1 for p in plans if p.due_at and p.due_at < now() and p.status not in ('CLOSED','MEASURED'))
    verified=sum(1 for e in ex if e.status=='VERIFIED'); certified=sum(1 for c in certs if c.status=='CERTIFIED')
    status='CRITICAL' if (report and report.status=='CRITICAL') else ('ATTENTION' if overdue or certified<verified else 'PASS')
    return {'schema':'v0.93','status':status,'kpis':{'recommendations':len(recs),'plans':len(plans),'executions':len(ex),'certifications':certified,'overdue_plans':overdue,'verified_executions':verified},'counts':{'recommendations':_counts(recs),'plans':_counts(plans),'executions':_counts(ex)},'latest_executive_audit_id':report.id if report else None}

def build_queue(db: Session):
    plans=db.query(ContinuousImprovementPlan).all(); recs={r.id:r for r in db.query(ContinuousImprovementRecommendation).all()}
    rows=[]
    for p in plans:
        if p.status in ('CLOSED','MEASURED'): continue
        r=recs.get(p.recommendation_id); priority=(r.pattern_code if r else 'UNKNOWN')
        due=p.due_at.isoformat() if p.due_at else None
        urgency=30 if p.due_at and p.due_at < now() else (20 if p.due_at else 10)
        rows.append({'plan_id':p.id,'recommendation_id':p.recommendation_id,'status':p.status,'priority_signal':priority,'urgency':urgency,'assigned_to':p.assigned_to,'due_at':due})
    rows.sort(key=lambda x:(-x['urgency'], x['plan_id']))
    return {'schema':'v0.94','status':'ATTENTION' if any(x['urgency']>=30 for x in rows) else 'PASS','items':rows,'total':len(rows)}

def build_kpi(db: Session):
    plans=db.query(ContinuousImprovementPlan).all(); ex=db.query(ContinuousImprovementExecution).all(); certs=db.query(ContinuousImprovementCertification).all()
    total=len(plans); closed=sum(1 for p in plans if p.status in ('CLOSED','MEASURED')); verified=sum(1 for e in ex if e.status=='VERIFIED'); certified=sum(1 for c in certs if c.status=='CERTIFIED')
    return {'schema':'v0.95','status':'PASS','metrics':{'closure_rate':round(closed/total*100,2) if total else 100.0,'verification_rate':round(verified/len(ex)*100,2) if ex else 100.0,'certification_rate':round(certified/verified*100,2) if verified else 100.0,'open_plans':total-closed}}

def build_sla(db: Session):
    plans=db.query(ContinuousImprovementPlan).all(); active=[p for p in plans if p.status not in ('CLOSED','MEASURED')]; overdue=[p for p in active if p.due_at and p.due_at < now()]
    rate=(len(overdue)/len(active)*100) if active else 0.0
    return {'schema':'v0.96','status':'CRITICAL' if rate>=50 else ('ATTENTION' if overdue else 'PASS'),'active':len(active),'overdue':len(overdue),'overdue_rate':round(rate,2)}

def build_compliance(db: Session):
    ex=db.query(ContinuousImprovementExecution).all(); certs={c.execution_id:c for c in db.query(ContinuousImprovementCertification).all()}
    missing=[e.id for e in ex if e.status=='VERIFIED' and e.id not in certs]
    return {'schema':'v0.97','status':'CRITICAL' if missing else 'PASS','verified_without_certificate':missing,'total_verified':sum(e.status=='VERIFIED' for e in ex)}

def build_export(db: Session):
    dash=build_dashboard(db); queue=build_queue(db); kpi=build_kpi(db); sla=build_sla(db); comp=build_compliance(db)
    return {'schema':'v0.98','generated_at':now().isoformat(),'dashboard':dash,'queue':queue,'kpi':kpi,'sla':sla,'compliance':comp}

def build_readiness(db: Session):
    checks={
      'executive_audit_snapshot': db.query(ContinuousImprovementExecutiveAuditSnapshot).count()>0,
      'production_admin': db.query(User).filter(User.role=='ADMIN',User.is_active==True).count()>0,
      'no_compliance_critical': build_compliance(db)['status']!='CRITICAL',
      'no_sla_critical': build_sla(db)['status']!='CRITICAL',
    }
    status='PASS' if all(checks.values()) else ('CRITICAL' if not checks['production_admin'] else 'ATTENTION')
    return {'schema':'v0.99','status':status,'checks':checks,'ready':status=='PASS'}

def build_release(db: Session):
    readiness=build_readiness(db); export=build_export(db); return {'schema':'v1.0','status':'RELEASE_CANDIDATE' if readiness['ready'] else 'BLOCKED','readiness':readiness,'export_hash':digest(export),'scope':['v0.93','v0.94','v0.95','v0.96','v0.97','v0.98','v0.99','v1.0']}

def _persist(db, model, data, actor_id, snapshot_date=None, release_version=None):
    payload=dict(data); payload.pop('generated_at',None)
    kwargs={'status':data['status'],'snapshot_json':canonical(data),'snapshot_hash':digest(payload),'generated_by':actor_id,'created_at':now()}
    if snapshot_date is not None: kwargs['snapshot_date']=snapshot_date
    if release_version is not None: kwargs['release_version']=release_version
    if snapshot_date is not None:
        row=db.query(model).filter_by(snapshot_date=snapshot_date).first()
    elif release_version is not None:
        row=db.query(model).filter_by(release_version=release_version).order_by(model.id.desc()).first()
    else:
        row=None
    if row is None:
        row=model(**kwargs); db.add(row); db.flush()
    else:
        for k,v in kwargs.items(): setattr(row,k,v)
        db.flush()
    db.add(AuditLog(actor_user_id=actor_id,action='CONTINUOUS_IMPROVEMENT_FINALIZATION_SNAPSHOT_CREATED',entity_type=model.__name__,entity_id=str(row.id),details=canonical({'status':row.status,'snapshot_hash':row.snapshot_hash}))); return row,data

def persist_all(db:Session, actor_id:int|None=None):
    d=[(ContinuousImprovementDashboardExecutiveSnapshot,build_dashboard(db)),(ContinuousImprovementActionQueueSnapshot,build_queue(db)),(ContinuousImprovementKpiSnapshot,build_kpi(db)),(ContinuousImprovementSlaSnapshot,build_sla(db)),(ContinuousImprovementComplianceSnapshot,build_compliance(db)),(ContinuousImprovementExportSnapshot,build_export(db)),(ContinuousImprovementProductionReadinessSnapshot,build_readiness(db))]
    rows={}
    for model,data in d:
        row,_=_persist(db,model,data,actor_id,date.today()); rows[model.__name__]=row.id
    rel,_=_persist(db,ContinuousImprovementProgramReleaseSnapshot,build_release(db),actor_id,release_version='1.0.0'); rows['release_id']=rel.id
    return rows

def export_csv(db:Session):
    data=build_export(db); out=io.StringIO(); w=csv.writer(out); w.writerow(['section','metric','value'])
    for section,obj in data.items():
        if isinstance(obj,dict):
            for k,v in obj.items(): w.writerow([section,k,canonical(v) if isinstance(v,(dict,list)) else v])
        else: w.writerow([section,'value',obj])
    return out.getvalue()
