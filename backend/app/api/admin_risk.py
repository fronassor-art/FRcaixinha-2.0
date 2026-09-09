from decimal import Decimal, ROUND_HALF_UP
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import Group, AuditLog, Member
from app.services.risk_v036 import evaluate_release, cash_balance, exposure
from app.models import Loan

router = APIRouter(prefix='/admin/risk', tags=['admin-risk'])

class RiskLimitsIn(BaseModel):
    min_cash_reserve: Decimal = Field(default=Decimal('0.00'), ge=0)
    max_member_exposure: Decimal | None = Field(default=None, ge=0)
    max_global_exposure: Decimal | None = Field(default=None, ge=0)
    max_exposure_ratio: Decimal | None = Field(default=None, gt=0, le=10)
    max_loan_amount: Decimal | None = Field(default=None, ge=0)
    max_loan_income_multiple: Decimal | None = Field(default=None, ge=0)

@router.get('/groups/{group_id}')
def get_risk(group_id: int, admin=Depends(require_admin), db: Session=Depends(get_db)):
    group = db.get(Group, group_id)
    if not group: raise HTTPException(404, 'Grupo não encontrado')
    return {
        'group_id': group.id,
        'limits': {'min_cash_reserve': str(group.min_cash_reserve), 'max_member_exposure': str(group.max_member_exposure) if group.max_member_exposure is not None else None, 'max_global_exposure': str(group.max_global_exposure) if group.max_global_exposure is not None else None, 'max_exposure_ratio': str(group.max_exposure_ratio) if group.max_exposure_ratio is not None else None, 'max_loan_amount': str(group.max_loan_amount) if group.max_loan_amount is not None else None, 'max_loan_income_multiple': str(group.max_loan_income_multiple) if group.max_loan_income_multiple is not None else None},
        'current': {'cash_balance': str(cash_balance(db)), 'global_exposure': str(exposure(db))},
    }

@router.put('/groups/{group_id}')
def update_risk(group_id: int, data: RiskLimitsIn, admin=Depends(require_admin), db: Session=Depends(get_db)):
    group = db.get(Group, group_id)
    if not group: raise HTTPException(404, 'Grupo não encontrado')
    group.min_cash_reserve = data.min_cash_reserve.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    group.max_member_exposure = data.max_member_exposure.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) if data.max_member_exposure is not None else None
    group.max_global_exposure = data.max_global_exposure.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) if data.max_global_exposure is not None else None
    group.max_exposure_ratio = data.max_exposure_ratio
    group.max_loan_amount = data.max_loan_amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) if data.max_loan_amount is not None else None
    group.max_loan_income_multiple = data.max_loan_income_multiple
    db.add(AuditLog(actor_user_id=admin.id, action='RISK_LIMITS_UPDATE', entity_type='GROUP', entity_id=str(group.id), details='risk limits updated'))
    db.commit(); db.refresh(group)
    return get_risk(group_id, admin, db)

@router.get('/preview-release/{loan_id}')
def preview_release(loan_id: int, admin=Depends(require_admin), db: Session=Depends(get_db)):
    loan = db.get(Loan, loan_id)
    if not loan: raise HTTPException(404, 'Empréstimo não encontrado')
    member = db.get(Member, loan.member_id)
    group = db.get(Group, member.group_id) if member else None
    if not group: raise HTTPException(409, 'Grupo não encontrado')
    return evaluate_release(db, loan, group)
