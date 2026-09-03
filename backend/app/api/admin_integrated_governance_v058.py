from __future__ import annotations
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import IntegratedGovernanceSnapshot
from app.services.integrated_governance_v058 import evaluate_integrated, persist_snapshot
router=APIRouter(prefix='/admin/integrated-governance',tags=['integrated-governance-v058'])

@router.get('/loan/{loan_id}')
def preview(loan_id:int,horizon_months:int=Query(12,ge=1,le=36),scenario:str=Query('BASE'),admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: return evaluate_integrated(db,loan_id,horizon_months=horizon_months,scenario=scenario)
    except ValueError as e: raise HTTPException(400,str(e))

@router.post('/snapshot/loan/{loan_id}')
def snapshot(loan_id:int,horizon_months:int=Query(12,ge=1,le=36),scenario:str=Query('BASE'),admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: result=evaluate_integrated(db,loan_id,horizon_months=horizon_months,scenario=scenario); row=persist_snapshot(db,result=result,actor_id=admin.id); db.commit(); db.refresh(row); return {'id':row.id,**result}
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))

@router.get('/history')
def history(group_id:int|None=None,loan_id:int|None=None,decision:str|None=None,limit:int=Query(50,ge=1,le=200),admin=Depends(require_admin),db:Session=Depends(get_db)):
    q=db.query(IntegratedGovernanceSnapshot).order_by(IntegratedGovernanceSnapshot.created_at.desc())
    if group_id is not None:q=q.filter(IntegratedGovernanceSnapshot.group_id==group_id)
    if loan_id is not None:q=q.filter(IntegratedGovernanceSnapshot.loan_id==loan_id)
    if decision:q=q.filter(IntegratedGovernanceSnapshot.final_decision==decision.upper())
    rows=q.limit(limit).all()
    return {'items':[{'id':r.id,'group_id':r.group_id,'loan_id':r.loan_id,'member_id':r.member_id,'final_decision':r.final_decision,'scenario':r.scenario,'horizon_months':r.horizon_months,'snapshot_hash':r.snapshot_hash,'created_at':r.created_at.isoformat()} for r in rows]}

@router.get('/snapshot/{snapshot_id}')
def detail(snapshot_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    row=db.get(IntegratedGovernanceSnapshot,snapshot_id)
    if not row: raise HTTPException(404,'Snapshot integrado não encontrado.')
    return {'id':row.id,'snapshot_hash':row.snapshot_hash,'data':json.loads(row.snapshot_json)}
