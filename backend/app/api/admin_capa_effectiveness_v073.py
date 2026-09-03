from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import CorrectiveActionPlan
from app.services.capa_effectiveness_v073 import create_review, sync_capa_recurrence, monitor_all
router=APIRouter(prefix='/admin/capa-effectiveness',tags=['capa-v073'])
class ReviewIn(BaseModel): result:str; score:int|None=None; notes:str|None=None
@router.post('/{capa_id}/reviews')
def review(capa_id:int,b:ReviewIn,admin=Depends(require_admin),db:Session=Depends(get_db)):
    c=db.get(CorrectiveActionPlan,capa_id)
    if not c: raise HTTPException(404,'CAPA não encontrada.')
    try:
        r=create_review(db,c,admin.id,b.result,b.score,b.notes); db.commit(); return {'id':r.id,'capa_id':r.capa_id,'result':r.result,'score':r.score,'notes':r.notes,'reviewed_at':r.reviewed_at.isoformat()}
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
@router.post('/sync')
def sync(admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: result=sync_capa_recurrence(db,actor_id=admin.id); db.commit(); return result
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
@router.get('/monitoring')
def monitoring(admin=Depends(require_admin),db:Session=Depends(get_db)):
    return monitor_all(db)
