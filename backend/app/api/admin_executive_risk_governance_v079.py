from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import ExecutiveRiskDecision, ExecutiveRiskDecisionGovernance
from app.services.executive_risk_governance_v079 import build_governance, approve, validate
router=APIRouter(prefix='/admin/executive-risk-governance',tags=['executive-risk-governance-v079'])
class Conditions(BaseModel): conditions:str|None=None
@router.post('/sync')
def sync(admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=db.query(ExecutiveRiskDecision).all(); created=0
    for d in rows:
        if not db.query(ExecutiveRiskDecisionGovernance).filter_by(decision_id=d.id).first(): build_governance(db,d); created+=1
    db.commit(); return {'created':created}
@router.get('')
def listing(status:str|None=None,limit:int=Query(100,ge=1,le=500),admin=Depends(require_admin),db:Session=Depends(get_db)):
    q=db.query(ExecutiveRiskDecisionGovernance).order_by(ExecutiveRiskDecisionGovernance.created_at.desc())
    if status:q=q.filter(ExecutiveRiskDecisionGovernance.status==status.upper())
    rows=q.limit(limit).all()
    return {'items':[{'id':r.id,'decision_id':r.decision_id,'required_approvals':r.required_approvals,'approvals_count':r.approvals_count,'status':r.status,'conflict_status':r.conflict_status,'conditions_required':r.conditions_required,'primary_approver_id':r.primary_approver_id,'secondary_approver_id':r.secondary_approver_id,'validation_status':r.validation_status,'validated_by':r.validated_by,'validated_at':r.validated_at,'integrity_hash':r.integrity_hash} for r in rows]}
@router.post('/{governance_id}/approve')
def approve_governance(governance_id:int,body:Conditions,admin=Depends(require_admin),db:Session=Depends(get_db)):
    row=db.get(ExecutiveRiskDecisionGovernance,governance_id)
    if not row: raise HTTPException(404,'governance_not_found')
    try: row=approve(db,row,admin.id,body.conditions); db.commit(); return {'id':row.id,'status':row.status,'approvals_count':row.approvals_count,'required_approvals':row.required_approvals,'integrity_hash':row.integrity_hash}
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
@router.post('/{governance_id}/validate')
def validate_governance(governance_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    row=db.get(ExecutiveRiskDecisionGovernance,governance_id)
    if not row: raise HTTPException(404,'governance_not_found')
    try: row=validate(db,row,admin.id); db.commit(); return {'id':row.id,'status':row.status,'validation_status':row.validation_status,'integrity_hash':row.integrity_hash}
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
