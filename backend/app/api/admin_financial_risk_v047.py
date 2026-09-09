from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import Loan, FinancialRiskAssessment
from app.services.financial_risk_v047 import assess_loan
router=APIRouter(prefix='/admin/financial-risk',tags=['financial-risk'])
@router.get('/preview/loan/{loan_id}')
def preview_loan(loan_id:int, admin=Depends(require_admin), db:Session=Depends(get_db)):
    loan=db.get(Loan,loan_id)
    if not loan: raise HTTPException(404,'Empréstimo não encontrado')
    return assess_loan(db,loan,persist=False)
@router.post('/assess/loan/{loan_id}')
def assess(loan_id:int, admin=Depends(require_admin), db:Session=Depends(get_db)):
    loan=db.get(Loan,loan_id)
    if not loan: raise HTTPException(404,'Empréstimo não encontrado')
    return assess_loan(db,loan,persist=True)
@router.get('/assessments')
def assessments(limit:int=Query(100,ge=1,le=500), admin=Depends(require_admin), db:Session=Depends(get_db)):
    rows=db.query(FinancialRiskAssessment).order_by(FinancialRiskAssessment.created_at.desc()).limit(limit).all()
    return {'items':[{'id':r.id,'subject_type':r.subject_type,'subject_id':r.subject_id,'member_id':r.member_id,'score':r.score,'status':r.status,'reasons':r.reasons,'created_at':r.created_at.isoformat()} for r in rows]}
@router.get('/summary')
def summary(admin=Depends(require_admin), db:Session=Depends(get_db)):
    rows=db.query(FinancialRiskAssessment).all()
    return {'total':len(rows),'pass':sum(r.status=='PASS' for r in rows),'review':sum(r.status=='REVIEW' for r in rows),'blocked':sum(r.status=='BLOCKED' for r in rows),'max_score':max((r.score for r in rows),default=0)}
