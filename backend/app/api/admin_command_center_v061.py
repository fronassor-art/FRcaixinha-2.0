from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import OperationalActionRecord
from app.services.operational_control_v060 import build_operational_control
from app.services.command_center_v061 import enrich,get_action,acknowledge
from pydantic import BaseModel
router=APIRouter(prefix='/admin/command-center',tags=['command-center-v061'])
class AckIn(BaseModel): note:str|None=None
@router.get('')
def command_center(admin=Depends(require_admin),db:Session=Depends(get_db)):
    data=build_operational_control(db); data['schema']='v0.61'; data['actions']=enrich(data['actions'])
    return data
@router.get('/actions/{code}')
def action(code:str,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try:return get_action(db,code.upper())
    except ValueError as e:raise HTTPException(404,str(e))
@router.post('/actions/{code}/acknowledge')
def ack(code:str,body:AckIn,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try:
        row=acknowledge(db,code=code.upper(),actor_id=admin.id,note=body.note);db.commit();db.refresh(row)
        return {'id':row.id,'code':row.action_code,'status':row.status,'acknowledged_by':row.acknowledged_by,'acknowledged_at':row.acknowledged_at.isoformat()}
    except ValueError as e:db.rollback();raise HTTPException(404,str(e))
@router.get('/actions/history')
def history(status:str|None=None,code:str|None=None,limit:int=Query(100,ge=1,le=500),admin=Depends(require_admin),db:Session=Depends(get_db)):
    q=db.query(OperationalActionRecord).order_by(OperationalActionRecord.created_at.desc())
    if status:q=q.filter(OperationalActionRecord.status==status.upper())
    if code:q=q.filter(OperationalActionRecord.action_code==code.upper())
    rows=q.limit(limit).all()
    return {'items':[{'id':r.id,'code':r.action_code,'status':r.status,'acknowledged_by':r.acknowledged_by,'acknowledged_at':r.acknowledged_at.isoformat() if r.acknowledged_at else None,'note':r.note,'created_at':r.created_at.isoformat()} for r in rows]}
