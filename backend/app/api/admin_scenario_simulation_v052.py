import json
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import ScenarioSimulationSnapshot
from app.services.scenario_simulator_v052 import simulate, snapshot_hash
router=APIRouter(prefix='/admin/scenario-simulation',tags=['scenario-simulation-v052'])

def params(horizon_months,scenario,contribution_factor,delinquency_rate,new_loan_disbursements,extra_expenses,reserve_floor):
    return dict(horizon_months=horizon_months,scenario=scenario,contribution_factor=contribution_factor,delinquency_rate=delinquency_rate,new_loan_disbursements=new_loan_disbursements,extra_expenses=extra_expenses,reserve_floor=reserve_floor)

@router.get('')
def run(horizon_months:int=Query(12,ge=1,le=36),scenario:str=Query('BASE'),contribution_factor:float=Query(1,ge=0),delinquency_rate:float=Query(0,ge=0,le=1),new_loan_disbursements:float=Query(0,ge=0),extra_expenses:float=Query(0,ge=0),reserve_floor:float=Query(0,ge=0),admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: return simulate(db,**params(horizon_months,scenario,contribution_factor,delinquency_rate,new_loan_disbursements,extra_expenses,reserve_floor))
    except ValueError as e: raise HTTPException(400,str(e))

@router.post('/snapshot')
def snapshot(horizon_months:int=Query(12,ge=1,le=36),scenario:str=Query('BASE'),contribution_factor:float=Query(1,ge=0),delinquency_rate:float=Query(0,ge=0,le=1),new_loan_disbursements:float=Query(0,ge=0),extra_expenses:float=Query(0,ge=0),reserve_floor:float=Query(0,ge=0),admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: data=simulate(db,**params(horizon_months,scenario,contribution_factor,delinquency_rate,new_loan_disbursements,extra_expenses,reserve_floor))
    except ValueError as e: raise HTTPException(400,str(e))
    raw=json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False); h=snapshot_hash(data)
    row=ScenarioSimulationSnapshot(as_of_date=__import__('datetime').date.fromisoformat(data['as_of_date']),horizon_months=horizon_months,scenario=scenario.upper(),status=data['status'],snapshot_json=raw,snapshot_hash=h,generated_by=admin.id); db.add(row); db.commit(); return {'id':row.id,'snapshot_hash':h,**data}

@router.get('/history')
def history(limit:int=Query(30,ge=1,le=200),admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=db.query(ScenarioSimulationSnapshot).order_by(ScenarioSimulationSnapshot.created_at.desc()).limit(limit).all()
    return {'items':[{'id':r.id,'as_of_date':r.as_of_date.isoformat(),'horizon_months':r.horizon_months,'scenario':r.scenario,'status':r.status,'snapshot_hash':r.snapshot_hash,'created_at':r.created_at.isoformat()} for r in rows]}
