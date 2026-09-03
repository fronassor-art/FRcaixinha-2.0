from fastapi import APIRouter,Depends,Query,HTTPException
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import ContinuousImprovementPrioritySnapshot
from app.services.continuous_improvement_priority_v085 import build_queue,persist
import json
router=APIRouter(prefix='/admin/continuous-improvement-priority',tags=['continuous-improvement-v085'])
@router.get('')
def current(admin=Depends(require_admin),db:Session=Depends(get_db)): return build_queue(db)
@router.post('/snapshot')
def snapshot(admin=Depends(require_admin),db:Session=Depends(get_db)):
 r,d=persist(db,admin.id); db.commit(); return {'id':r.id,'snapshot_hash':r.snapshot_hash,**d}
@router.post('/sync')
def sync(admin=Depends(require_admin),db:Session=Depends(get_db)):
 r,d=persist(db,admin.id); db.commit(); return {'id':r.id,'snapshot_hash':r.snapshot_hash,**d}
@router.get('/history')
def history(limit:int=Query(30,ge=1,le=200),admin=Depends(require_admin),db:Session=Depends(get_db)):
 rows=db.query(ContinuousImprovementPrioritySnapshot).order_by(ContinuousImprovementPrioritySnapshot.snapshot_date.desc()).limit(limit).all()
 return {'items':[{'id':r.id,'snapshot_date':r.snapshot_date.isoformat(),'status':r.status,'snapshot_hash':r.snapshot_hash} for r in rows]}
@router.get('/snapshot/{id}')
def detail(id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
 r=db.get(ContinuousImprovementPrioritySnapshot,id)
 if not r: raise HTTPException(404,'snapshot_not_found')
 return {'id':r.id,'snapshot_hash':r.snapshot_hash,**json.loads(r.snapshot_json)}
