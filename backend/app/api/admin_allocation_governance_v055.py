import json
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import AllocationPolicy, ResourceAllocationSnapshot, AuditLog
from app.services.allocation_governance_v055 import build_governance_allocation, snapshot_hash
from app.services.allocation_transparency_v056 import persist_transparency

router=APIRouter(prefix='/admin/allocation-governance',tags=['allocation-governance-v055'])

@router.get('/policy')
def policy(group_id:int, admin=Depends(require_admin), db:Session=Depends(get_db)):
    p=db.query(AllocationPolicy).filter(AllocationPolicy.group_id==group_id).first()
    if not p: return {'group_id':group_id,'exists':False,'defaults':{'quota_weight':'1.000','payment_history_weight':'1.000','tenure_weight':'0.250','risk_weight':'1.000','review_factor':'0.500','tie_breaker':'OLDEST_MEMBER'}}
    return {'id':p.id,'group_id':p.group_id,'name':p.name,'quota_weight':str(p.quota_weight),'payment_history_weight':str(p.payment_history_weight),'tenure_weight':str(p.tenure_weight),'risk_weight':str(p.risk_weight),'review_factor':str(p.review_factor),'tie_breaker':p.tie_breaker,'version':p.version,'active':p.active}

@router.put('/policy')
def upsert_policy(group_id:int, payload:dict, admin=Depends(require_admin), db:Session=Depends(get_db)):
    allowed={'name','quota_weight','payment_history_weight','tenure_weight','risk_weight','review_factor','tie_breaker','active'}
    if payload.get('tie_breaker','OLDEST_MEMBER') not in ('OLDEST_MEMBER','LOWEST_MEMBER_ID'): raise HTTPException(400,'Critério de desempate inválido.')
    p=db.query(AllocationPolicy).filter(AllocationPolicy.group_id==group_id).first()
    if not p:
        p=AllocationPolicy(group_id=group_id)
        db.add(p)
    for k,v in payload.items():
        if k in allowed: setattr(p,k, v)
    p.version = 1 if p.id is None else (p.version or 1) + 1
    db.add(AuditLog(actor_user_id=admin.id, action='ALLOCATION_POLICY_UPDATE', entity_type='ALLOCATION_POLICY', entity_id=str(group_id), details=json.dumps({'version':p.version,'changes':{k:v for k,v in payload.items() if k in allowed}}, ensure_ascii=False, default=str)))
    db.commit(); db.refresh(p)
    return policy(group_id,admin,db)

@router.get('')
def evaluate(group_id:int, capacity:float|None=Query(None,ge=0), requested_amount:float|None=Query(None,ge=0), admin=Depends(require_admin), db:Session=Depends(get_db)):
    try: return build_governance_allocation(db,group_id=group_id,capacity=Decimal(str(capacity)) if capacity is not None else None,requested_amount=Decimal(str(requested_amount)) if requested_amount is not None else None)
    except ValueError as e: raise HTTPException(400,str(e))

@router.post('/snapshot')
def snapshot(group_id:int, capacity:float|None=Query(None,ge=0), requested_amount:float|None=Query(None,ge=0), admin=Depends(require_admin), db:Session=Depends(get_db)):
    data=evaluate(group_id,capacity,requested_amount,admin,db); h=snapshot_hash(data)
    row=ResourceAllocationSnapshot(group_id=group_id,capacity=Decimal(str(data['capacity'])),allocated_total=Decimal(str(data['allocated_total'])),decision=data['decision'],method='GOVERNED_'+str(data['policy']['tie_breaker']),snapshot_json=json.dumps(data,sort_keys=True,separators=(',',':'),default=str),snapshot_hash=h,generated_by=admin.id)
    db.add(row); db.flush()
    transparency, explanation, eh = persist_transparency(db, resource_snapshot=row, allocation=data, actor_id=admin.id)
    db.add(AuditLog(actor_user_id=admin.id, action='ALLOCATION_TRANSPARENCY_SNAPSHOT', entity_type='ALLOCATION_TRANSPARENCY', entity_id=str(row.id), details=eh))
    db.commit(); db.refresh(transparency)
    return {'id':row.id,'snapshot_hash':h,'transparency_id':transparency.id,'explanation_hash':eh,**data}
