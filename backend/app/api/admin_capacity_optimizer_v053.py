import json
from datetime import date
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import LoanCapacitySnapshot
from app.services.capacity_optimizer_v053 import optimize_capacity, snapshot_hash

router=APIRouter(prefix='/admin/capacity-optimizer',tags=['capacity-optimizer-v053'])

def params(group_id,member_id,horizon_months,scenario,contribution_factor,delinquency_rate,extra_expenses,reserve_floor):
    return dict(group_id=group_id,member_id=member_id,horizon_months=horizon_months,scenario=scenario,contribution_factor=contribution_factor,delinquency_rate=delinquency_rate,extra_expenses=extra_expenses,reserve_floor=reserve_floor)

@router.get('')
def run(group_id:int|None=None,member_id:int|None=None,horizon_months:int=Query(12,ge=1,le=36),scenario:str=Query('BASE'),contribution_factor:float=Query(1,ge=0),delinquency_rate:float=Query(0,ge=0,le=1),extra_expenses:float=Query(0,ge=0),reserve_floor:float|None=Query(None,ge=0),admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: return optimize_capacity(db,**params(group_id,member_id,horizon_months,scenario,contribution_factor,delinquency_rate,extra_expenses,reserve_floor))
    except ValueError as e: raise HTTPException(400,str(e))

@router.post('/snapshot')
def snapshot(group_id:int|None=None,member_id:int|None=None,horizon_months:int=Query(12,ge=1,le=36),scenario:str=Query('BASE'),contribution_factor:float=Query(1,ge=0),delinquency_rate:float=Query(0,ge=0,le=1),extra_expenses:float=Query(0,ge=0),reserve_floor:float|None=Query(None,ge=0),admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: data=optimize_capacity(db,**params(group_id,member_id,horizon_months,scenario,contribution_factor,delinquency_rate,extra_expenses,reserve_floor))
    except ValueError as e: raise HTTPException(400,str(e))
    h=snapshot_hash(data); row=LoanCapacitySnapshot(group_id=group_id,member_id=member_id,as_of_date=date.fromisoformat(data['as_of_date']),horizon_months=horizon_months,scenario=scenario.upper(),decision=data['decision'],capacity=Decimal(data['capacity']),snapshot_json=json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False),snapshot_hash=h,generated_by=admin.id); db.add(row); db.commit(); return {'id':row.id,'snapshot_hash':h,**data}

@router.get('/history')
def history(limit:int=Query(30,ge=1,le=200),admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=db.query(LoanCapacitySnapshot).order_by(LoanCapacitySnapshot.created_at.desc()).limit(limit).all()
    return {'items':[{'id':r.id,'group_id':r.group_id,'member_id':r.member_id,'as_of_date':r.as_of_date.isoformat(),'horizon_months':r.horizon_months,'scenario':r.scenario,'decision':r.decision,'capacity':f'{Decimal(r.capacity):.2f}','snapshot_hash':r.snapshot_hash,'created_at':r.created_at.isoformat()} for r in rows]}
