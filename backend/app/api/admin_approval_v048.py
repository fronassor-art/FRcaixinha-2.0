from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import Loan
from app.services.approval_engine_v048 import evaluate_loan_pipeline

router=APIRouter(prefix='/admin/approval-engine',tags=['approval-engine'])

@router.get('/preview/loan/{loan_id}')
def preview(loan_id:int, admin=Depends(require_admin), db:Session=Depends(get_db)):
    loan=db.get(Loan,loan_id)
    if not loan: raise HTTPException(404,'Empréstimo não encontrado')
    return evaluate_loan_pipeline(db, loan, persist_risk=False, include_release=True)
