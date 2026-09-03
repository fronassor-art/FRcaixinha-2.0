import json
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import ResourceAllocationSnapshot
from app.services.resource_allocation_v054 import allocate_resources, snapshot_hash

router=APIRouter(prefix='/admin/resource-allocation',tags=['resource-allocation-v054'])

@router.get('')
def run(group_id:int, capacity:float|None=Query(None,ge=0), requested_amount:float|None=Query(None,ge=0), admin=Depends(require_admin), db:Session=Depends(get_db)):
    try: return allocate_resources(db,group_id=group_id,capacity=Decimal(str(capacity)) if capacity is not None else None,requested_amount=Decimal(str(requested_amount)) if requested_amount is not None else None)
    except ValueError as e: raise HTTPException(400,str(e))

@router.post('/snapshot')
def snapshot(group_id:int, capacity:float|None=Query(None,ge=0), requested_amount:float|None=Query(None,ge=0), admin=Depends(require_admin), db:Session=Depends(get_db)):
    try: data=allocate_resources(db,group_id=group_id,capacity=Decimal(str(capacity)) if capacity is not None else None,requested_amount=Decimal(str(requested_amount)) if requested_amount is not None else None)
    except ValueError as e: raise HTTPException(400,str(e))
    h=snapshot_hash(data); row=ResourceAllocationSnapshot(group_id=group_id,capacity=Decimal(str(data['capacity'])),allocated_total=Decimal(str(data['allocated_total'])),decision=data['decision'],method=data.get('method','PRO_RATA_QUOTA_RISK_AWARE'),snapshot_json=json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False,default=str),snapshot_hash=h,generated_by=admin.id); db.add(row); db.commit(); return {'id':row.id,'snapshot_hash':h,**data}

@router.get('/history')
def history(group_id:int|None=None,limit:int=Query(30,ge=1,le=200),admin=Depends(require_admin),db:Session=Depends(get_db)):
    q=db.query(ResourceAllocationSnapshot).order_by(ResourceAllocationSnapshot.created_at.desc())
    if group_id is not None: q=q.filter(ResourceAllocationSnapshot.group_id==group_id)
    rows=q.limit(limit).all()
    return {'items':[{'id':r.id,'group_id':r.group_id,'capacity':f'{Decimal(r.capacity):.2f}','allocated_total':f'{Decimal(r.allocated_total):.2f}','decision':r.decision,'method':r.method,'snapshot_hash':r.snapshot_hash,'created_at':r.created_at.isoformat()} for r in rows]}
