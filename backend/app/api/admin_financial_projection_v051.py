import json
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import FinancialProjectionSnapshot
from app.services.financial_projection_v051 import build_projection, persist_projection
router=APIRouter(prefix='/admin/financial-projection',tags=['financial-projection-v051'])
@router.get('')
def projection(horizon_months:int=Query(12,ge=1,le=36),scenario:str=Query('BASE'),admin=Depends(require_admin),db:Session=Depends(get_db)): return build_projection(db,horizon_months=horizon_months,scenario=scenario)
@router.post('/snapshot')
def snapshot(horizon_months:int=Query(12,ge=1,le=36),scenario:str=Query('BASE'),admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: row,data=persist_projection(db,admin.id,horizon_months=horizon_months,scenario=scenario); db.commit(); return {'id':row.id,'snapshot_hash':row.snapshot_hash,**data}
    except ValueError as e: raise HTTPException(400,str(e))
@router.get('/history')
def history(limit:int=Query(30,ge=1,le=200),admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=db.query(FinancialProjectionSnapshot).order_by(FinancialProjectionSnapshot.as_of_date.desc(),FinancialProjectionSnapshot.created_at.desc()).limit(limit).all()
    return {'items':[{'id':r.id,'as_of_date':r.as_of_date.isoformat(),'horizon_months':r.horizon_months,'scenario':r.scenario,'status':r.status,'snapshot_hash':r.snapshot_hash,'created_at':r.created_at.isoformat()} for r in rows]}
