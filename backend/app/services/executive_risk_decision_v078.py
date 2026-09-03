from __future__ import annotations
import hashlib,json
from datetime import datetime,timezone
from sqlalchemy.orm import Session
from app.models import ExecutiveRiskDecision, ExecutiveRiskResponseSnapshot, OperationalRiskAlert, OperationalRiskResponsePlan, AuditLog

def now(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,default=str)
def _hash(v): return hashlib.sha256(canonical(v).encode()).hexdigest()

def create_decision(db:Session, snapshot_id=None, alert_id=None, response_plan_id=None, requested_by=None, priority='MEDIUM', recommendation='', decision_type='OPERATIONAL_REVIEW'):
    if alert_id:
        alert=db.get(OperationalRiskAlert,alert_id)
        if not alert: raise ValueError('alert_not_found')
        if not recommendation: recommendation=alert.recommended_action
    if response_plan_id:
        plan=db.get(OperationalRiskResponsePlan,response_plan_id)
        if not plan: raise ValueError('response_plan_not_found')
    payload={'snapshot_id':snapshot_id,'alert_id':alert_id,'response_plan_id':response_plan_id,'priority':priority,'decision_type':decision_type,'recommendation':recommendation}
    row=ExecutiveRiskDecision(**payload,status='PENDING',requested_by=requested_by,decision_hash=_hash(payload),created_at=now(),updated_at=now())
    db.add(row); db.flush()
    if requested_by: db.add(AuditLog(actor_user_id=requested_by,action='EXECUTIVE_RISK_DECISION_CREATED',entity_type='ExecutiveRiskDecision',entity_id=str(row.id),details=f'priority={priority};type={decision_type}'))
    return row

def decide(db:Session,row:ExecutiveRiskDecision,decision:str,decided_by:int,rationale:str,conditions=None):
    decision=decision.upper()
    if decision not in ('APPROVE','REJECT','DEFER','ESCALATE'): raise ValueError('invalid_decision')
    if not rationale or not rationale.strip(): raise ValueError('rationale_required')
    if row.status=='DECIDED': raise ValueError('already_decided')
    row.status='DECIDED'; row.decision=decision; row.rationale=rationale.strip(); row.conditions=conditions; row.decided_by=decided_by; row.decided_at=now(); row.updated_at=now()
    row.decision_hash=_hash({'id':row.id,'snapshot_id':row.snapshot_id,'alert_id':row.alert_id,'response_plan_id':row.response_plan_id,'decision':decision,'rationale':row.rationale,'conditions':conditions,'decided_by':decided_by})
    db.add(AuditLog(actor_user_id=decided_by,action='EXECUTIVE_RISK_DECISION_MADE',entity_type='ExecutiveRiskDecision',entity_id=str(row.id),details=f'decision={decision}'))
    return row
