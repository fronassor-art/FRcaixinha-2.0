from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import ContinuousImprovementAssignmentCapacity, ContinuousImprovementAssignmentSnapshot, User
from app.services.continuous_improvement_balancing_v086 import build_balancing, persist
import json
router=APIRouter(prefix='/admin/continuous-improvement-balancing',tags=['continuous-improvement-v086'])
class CapacityIn(BaseModel):
    max_active_items:int=Field(ge=1,le=100)
    max_critical_items:int=Field(ge=0,le=100)
    enabled:bool=True
@router.get('')
def current(admin=Depends(require_admin),db:Session=Depends(get_db)): return build_balancing(db)
@router.post('/snapshot')
def snapshot(admin=Depends(require_admin),db:Session=Depends(get_db)):
    r,d=persist(db,admin.id); db.commit(); return {'id':r.id,'snapshot_hash':r.snapshot_hash,**d}
@router.post('/sync')
def sync(admin=Depends(require_admin),db:Session=Depends(get_db)):
    r,d=persist(db,admin.id); db.commit(); return {'id':r.id,'snapshot_hash':r.snapshot_hash,**d}
@router.get('/history')
def history(limit:int=Query(30,ge=1,le=200),admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=db.query(ContinuousImprovementAssignmentSnapshot).order_by(ContinuousImprovementAssignmentSnapshot.snapshot_date.desc()).limit(limit).all()
    return {'items':[{'id':r.id,'snapshot_date':r.snapshot_date.isoformat(),'status':r.status,'snapshot_hash':r.snapshot_hash} for r in rows]}
@router.get('/snapshot/{id}')
def detail(id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    r=db.get(ContinuousImprovementAssignmentSnapshot,id)
    if not r: raise HTTPException(404,'snapshot_not_found')
    return {'id':r.id,'snapshot_hash':r.snapshot_hash,**json.loads(r.snapshot_json)}
@router.get('/capacity')
def capacities(admin=Depends(require_admin),db:Session=Depends(get_db)):
    users=db.query(User).filter(User.role=='ADMIN').order_by(User.id.asc()).all(); caps={c.user_id:c for c in db.query(ContinuousImprovementAssignmentCapacity).all()}
    return {'items':[{'user_id':u.id,'name':u.name,'max_active_items':caps[u.id].max_active_items if u.id in caps else 5,'max_critical_items':caps[u.id].max_critical_items if u.id in caps else 1,'enabled':caps[u.id].enabled if u.id in caps else True} for u in users]}
@router.put('/capacity/{user_id}')
def set_capacity(user_id:int, body:CapacityIn, admin=Depends(require_admin),db:Session=Depends(get_db)):
    u=db.get(User,user_id)
    if not u or u.role!='ADMIN': raise HTTPException(404,'admin_not_found')
    c=db.query(ContinuousImprovementAssignmentCapacity).filter_by(user_id=user_id).first()
    if not c: c=ContinuousImprovementAssignmentCapacity(user_id=user_id); db.add(c)
    c.max_active_items=body.max_active_items; c.max_critical_items=body.max_critical_items; c.enabled=body.enabled
    db.commit(); return {'user_id':user_id,'max_active_items':c.max_active_items,'max_critical_items':c.max_critical_items,'enabled':c.enabled}
