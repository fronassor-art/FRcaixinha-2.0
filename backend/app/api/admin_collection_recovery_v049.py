from datetime import date
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import CollectionCase, PaymentPromise
from app.services.collection_recovery_v049 import sync_cases, create_promise, collection_recovery_summary
router=APIRouter(prefix='/admin/collection-recovery',tags=['collection-recovery-v049'])
class PromiseIn(BaseModel): amount: Decimal; promised_date: date; note: str|None=None
@router.get('/summary')
def summary(admin=Depends(require_admin),db:Session=Depends(get_db)): return collection_recovery_summary(db)
@router.post('/sync')
def sync(admin=Depends(require_admin),db:Session=Depends(get_db)):
    r=sync_cases(db); db.commit(); return r|{'summary':collection_recovery_summary(db)}
@router.get('/cases')
def cases(limit:int=100,admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=db.query(CollectionCase).order_by(CollectionCase.opened_at.desc()).limit(min(limit,500)).all()
    return [{'id':x.id,'member_id':x.member_id,'loan_id':x.loan_id,'status':x.status,'stage':x.stage,'opened_at':x.opened_at.isoformat(),'next_action_at':x.next_action_at.isoformat() if x.next_action_at else None} for x in rows]
@router.post('/cases/{case_id}/promise')
def promise(case_id:int,data:PromiseIn,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: p=create_promise(db,case_id,admin.id,data.amount,data.promised_date,data.note); db.commit(); return {'id':p.id,'case_id':p.case_id,'amount':str(p.promised_amount),'promised_date':p.promised_date.isoformat(),'status':p.status}
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
@router.get('/promises')
def promises(limit:int=100,admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=db.query(PaymentPromise).order_by(PaymentPromise.promised_date.asc()).limit(min(limit,500)).all()
    return [{'id':x.id,'case_id':x.case_id,'member_id':x.member_id,'amount':str(x.promised_amount),'promised_date':x.promised_date.isoformat(),'status':x.status} for x in rows]
