from datetime import date
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import CollectionEvent
from app.services.collections_v038 import run_collection_cycle, collections_summary

router=APIRouter(prefix='/admin/collections',tags=['admin-collections'])
@router.get('/summary')
def summary(admin=Depends(require_admin),db:Session=Depends(get_db)): return collections_summary(db)
@router.post('/run')
def run(admin=Depends(require_admin),db:Session=Depends(get_db)):
    result=run_collection_cycle(db); db.commit(); return result | {'summary':collections_summary(db)}
@router.get('/events')
def events(limit:int=100,admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=db.query(CollectionEvent).order_by(CollectionEvent.created_at.desc()).limit(min(limit,500)).all()
    return [{'id':r.id,'installment_id':r.installment_id,'member_id':r.member_id,'event_type':r.event_type,'event_date':r.event_date.isoformat(),'channel':r.channel,'created_at':r.created_at.isoformat()} for r in rows]
