from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone, date
from sqlalchemy.orm import Session
from app.models import (User, ContinuousImprovementRecommendation, ContinuousImprovementPlan,
                        ContinuousImprovementPrioritySnapshot, ContinuousImprovementAssignmentCapacity,
                        ContinuousImprovementAssignmentSnapshot)
from app.services.continuous_improvement_priority_v085 import build_queue

def now(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
def digest(v): return hashlib.sha256(canonical(v).encode()).hexdigest()

def _priority_map(queue): return {x['recommendation_id']: x for x in queue['items']}

def _eligible_admins(db: Session):
    users = db.query(User).filter(User.role == 'ADMIN', User.is_active.is_(True)).order_by(User.id.asc()).all()
    caps = {c.user_id: c for c in db.query(ContinuousImprovementAssignmentCapacity).all()}
    out=[]
    for u in users:
        c=caps.get(u.id)
        if c is not None and not c.enabled: continue
        out.append({'user_id':u.id,'name':u.name,'max_active_items':c.max_active_items if c else 5,'max_critical_items':c.max_critical_items if c else 1})
    return out

def candidate_order(candidates, loads, critical_loads):
    return sorted(candidates, key=lambda c:(loads[c['user_id']]/max(1,c['max_active_items']), critical_loads[c['user_id']], loads[c['user_id']], c['user_id']))

def build_balancing(db: Session):
    queue=build_queue(db); pmap=_priority_map(queue)
    plans=db.query(ContinuousImprovementPlan).filter(ContinuousImprovementPlan.status != 'CLOSED').all()
    recs={r.id:r for r in db.query(ContinuousImprovementRecommendation).all()}
    admins=_eligible_admins(db)
    loads={a['user_id']:0 for a in admins}; critical_loads={a['user_id']:0 for a in admins}
    for p in plans:
        if p.assigned_to in loads:
            loads[p.assigned_to]+=1
            item=pmap.get(p.recommendation_id)
            if item and item['priority']=='CRITICAL': critical_loads[p.assigned_to]+=1
    assignments=[]; unassigned=[]
    # Work on the same deterministic priority queue produced by v0.85.
    for item in queue['items']:
        if item['priority'] not in ('CRITICAL','HIGH','MEDIUM','LOW'): continue
        rec=recs.get(item['recommendation_id'])
        plan=next((p for p in plans if p.recommendation_id==item['recommendation_id']),None)
        if plan and plan.assigned_to:
            assignments.append({'recommendation_id':item['recommendation_id'],'plan_id':plan.id,'priority':item['priority'],'priority_score':item['priority_score'],'assigned_to':plan.assigned_to,'current_load':loads.get(plan.assigned_to,0),'capacity':next((a['max_active_items'] for a in admins if a['user_id']==plan.assigned_to),None),'decision':'KEEP_EXISTING','rationale':'Plano já possui responsável; nenhuma redistribuição automática é feita.'})
            continue
        candidates=[]
        for a in admins:
            uid=a['user_id']
            if loads[uid] >= a['max_active_items']: continue
            if item['priority']=='CRITICAL' and critical_loads[uid] >= a['max_critical_items']: continue
            # Conflict avoidance: decision maker / implementer should not receive the same improvement item.
            if rec and uid in {rec.decided_by, rec.implemented_by}: continue
            candidates.append(a)
        if not candidates:
            unassigned.append({'recommendation_id':item['recommendation_id'],'priority':item['priority'],'priority_score':item['priority_score'],'reason':'NO_ELIGIBLE_CAPACITY_OR_CONFLICT'})
            continue
        chosen=candidate_order(candidates,loads,critical_loads)[0]; uid=chosen['user_id']
        before=loads[uid]; loads[uid]+=1
        if item['priority']=='CRITICAL': critical_loads[uid]+=1
        assignments.append({'recommendation_id':item['recommendation_id'],'plan_id':plan.id if plan else None,'priority':item['priority'],'priority_score':item['priority_score'],'assigned_to':uid,'current_load':before,'capacity':chosen['max_active_items'],'projected_load':loads[uid],'decision':'RECOMMEND','rationale':f"Menor carga relativa elegível; capacidade {chosen['max_active_items']} e carga projetada {loads[uid]}/{chosen['max_active_items']}."})
    status='CRITICAL' if unassigned else ('ATTENTION' if any(a['decision']=='RECOMMEND' and a['priority']=='CRITICAL' for a in assignments) else 'PASS')
    return {'schema':'v0.86','generated_at':now().isoformat(),'priority_snapshot':{'risk_score':queue['risk_score'],'counts':queue['counts']},'capacity':{'administrators':len(admins),'total_capacity':sum(a['max_active_items'] for a in admins),'active_load':sum(loads.values()),'available_capacity':sum(max(0,a['max_active_items']-loads[a['user_id']]) for a in admins)},'assignments':assignments,'unassigned':unassigned,'status':status}

def persist(db: Session, actor_id: int|None=None, snapshot_date: date|None=None):
    d=build_balancing(db); sd=snapshot_date or now().date(); payload=dict(d); payload.pop('generated_at',None)
    h=digest({'schema':d['schema'],'snapshot_date':sd.isoformat(),'payload':payload})
    row=db.query(ContinuousImprovementAssignmentSnapshot).filter_by(snapshot_date=sd).first()
    if row:
        row.status=d['status']; row.snapshot_json=canonical({'snapshot_date':sd.isoformat(),**d,'generated_at':None}); row.snapshot_hash=h; row.generated_by=actor_id; row.updated_at=now()
    else:
        row=ContinuousImprovementAssignmentSnapshot(snapshot_date=sd,status=d['status'],snapshot_json=canonical({'snapshot_date':sd.isoformat(),**d,'generated_at':None}),snapshot_hash=h,generated_by=actor_id,created_at=now(),updated_at=now()); db.add(row); db.flush()
    return row,d
