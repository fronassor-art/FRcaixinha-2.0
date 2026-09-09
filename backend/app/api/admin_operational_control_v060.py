import json
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import OperationalControlSnapshot
from app.services.operational_control_v060 import build_operational_control, persist_operational_control
router=APIRouter(prefix='/admin/operational-control',tags=['operational-control-v060'])
@router.get('')
def current(admin=Depends(require_admin),db:Session=Depends(get_db)): return build_operational_control(db)
@router.post('/snapshot')
def snapshot(admin=Depends(require_admin),db:Session=Depends(get_db)):
    row,data=persist_operational_control(db,admin.id); db.commit(); return {'id':row.id,'snapshot_hash':row.snapshot_hash,**data}
@router.get('/history')
def history(limit:int=Query(30,ge=1,le=200),admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=db.query(OperationalControlSnapshot).order_by(OperationalControlSnapshot.snapshot_date.desc()).limit(limit).all()
    return {'items':[{'id':r.id,'snapshot_date':r.snapshot_date.isoformat(),'status':r.status,'action_count':r.action_count,'snapshot_hash':r.snapshot_hash,'created_at':r.created_at.isoformat()} for r in rows]}
@router.get('/snapshot/{snapshot_id}')
def detail(snapshot_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    row=db.get(OperationalControlSnapshot,snapshot_id)
    if not row: from fastapi import HTTPException; raise HTTPException(404,'Snapshot não encontrado')
    return {'id':row.id,'snapshot_hash':row.snapshot_hash,**json.loads(row.snapshot_json)}
