import json
from datetime import date
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import GovernanceSnapshot
from app.services.governance_v041 import build_executive_governance, persist_governance_snapshot

router=APIRouter(prefix='/admin/governance',tags=['admin-governance'])

@router.get('/executive')
def executive(admin=Depends(require_admin),db:Session=Depends(get_db)):
    return build_executive_governance(db)

@router.post('/snapshot')
def snapshot(admin=Depends(require_admin),db:Session=Depends(get_db)):
    row,data=persist_governance_snapshot(db,admin.id); db.commit()
    return {'id':row.id,'snapshot_hash':row.snapshot_hash,**data}

@router.get('/history')
def history(limit:int=Query(30,ge=1,le=200),admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=db.query(GovernanceSnapshot).order_by(GovernanceSnapshot.snapshot_date.desc()).limit(limit).all()
    return {'items':[{'id':r.id,'snapshot_date':r.snapshot_date.isoformat(),'status':r.status,'snapshot_hash':r.snapshot_hash,'created_at':r.created_at.isoformat()} for r in rows]}
