from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import ExecutiveRiskEffectiveness, ContinuousImprovementRecommendation, AuditLog

def now(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
def digest(v): return hashlib.sha256(canonical(v).encode()).hexdigest()

def _hash(r):
    r.integrity_hash = digest({'id':r.id,'indicator_code':r.indicator_code,'pattern_code':r.pattern_code,'status':r.status,'sample_size':r.sample_size,'effective_count':r.effective_count,'partial_count':r.partial_count,'ineffective_count':r.ineffective_count,'avg_delta':r.avg_delta,'recommendation':r.recommendation,'decision':r.decision,'implemented_at':r.implemented_at})

def analyze(db: Session, actor_id: int|None = None):
    rows = db.query(ExecutiveRiskEffectiveness).filter(ExecutiveRiskEffectiveness.status=='VERIFIED', ExecutiveRiskEffectiveness.effectiveness_result.isnot(None)).all()
    by={}
    for r in rows:
        key=r.indicator_code or 'UNKNOWN'; by.setdefault(key,[]).append(r)
    created=[]
    for indicator, items in by.items():
        n=len(items); eff=sum(x.effectiveness_result=='EFFECTIVE' for x in items); part=sum(x.effectiveness_result=='PARTIAL' for x in items); ineff=n-eff-part
        deltas=[x.delta_score for x in items if x.delta_score is not None]
        avg=sum(deltas)/len(deltas) if deltas else None
        if n < 2: continue
        pattern='INEFFECTIVE_PATTERN' if ineff/n >= .5 else ('PARTIAL_PATTERN' if (ineff+part)/n >= .5 else ('NEGATIVE_DELTA_PATTERN' if avg is not None and avg > 0 else 'STABLE_PATTERN'))
        if pattern=='STABLE_PATTERN': continue
        existing=db.query(ContinuousImprovementRecommendation).filter_by(indicator_code=indicator, pattern_code=pattern, status='OPEN').first()
        if existing: continue
        rec=('Revisar o controle e executar nova ação corretiva com critério de efetividade mais específico.' if pattern=='INEFFECTIVE_PATTERN' else
             'Ajustar o controle e repetir a medição após prazo definido.' if pattern=='PARTIAL_PATTERN' else
             'Investigar a causa do aumento do risco e revisar o plano de resposta antes de nova execução.')
        row=ContinuousImprovementRecommendation(indicator_code=indicator,pattern_code=pattern,status='OPEN',sample_size=n,effective_count=eff,partial_count=part,ineffective_count=ineff,avg_delta=avg,recommendation=rec,integrity_hash='',created_at=now(),updated_at=now())
        db.add(row);db.flush();_hash(row);created.append(row)
        db.add(AuditLog(actor_user_id=actor_id,action='CONTINUOUS_IMPROVEMENT_RECOMMENDATION_CREATED',entity_type='ContinuousImprovementRecommendation',entity_id=str(row.id),details=canonical({'indicator_code':indicator,'pattern_code':pattern,'sample_size':n})))
    return created

def decide(db: Session, row, actor_id:int, decision:str, note:str):
    if row.status not in {'OPEN','ACKNOWLEDGED'}: raise ValueError('invalid_status_for_decision')
    if decision not in {'ACCEPT','REJECT','DEFER'}: raise ValueError('invalid_decision')
    if not note.strip(): raise ValueError('decision_note_required')
    row.decision=decision; row.decision_note=note.strip(); row.decided_by=actor_id; row.decided_at=now(); row.status={'ACCEPT':'ACCEPTED','REJECT':'REJECTED','DEFER':'DEFERRED'}[decision];row.updated_at=now();_hash(row)
    db.add(AuditLog(actor_user_id=actor_id,action='CONTINUOUS_IMPROVEMENT_DECIDED',entity_type='ContinuousImprovementRecommendation',entity_id=str(row.id),details=canonical({'decision':decision})))
    return row

def implement(db: Session,row,actor_id:int,note:str):
    if row.status!='ACCEPTED': raise ValueError('recommendation_must_be_accepted')
    if not note.strip(): raise ValueError('implementation_note_required')
    row.status='IMPLEMENTED'; row.implementation_note=note.strip();row.implemented_by=actor_id;row.implemented_at=now();row.updated_at=now();_hash(row)
    db.add(AuditLog(actor_user_id=actor_id,action='CONTINUOUS_IMPROVEMENT_IMPLEMENTED',entity_type='ContinuousImprovementRecommendation',entity_id=str(row.id),details=canonical({'implemented':True})))
    return row
