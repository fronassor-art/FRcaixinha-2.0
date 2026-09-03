from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.api.deps import require_admin
from app.models import Group, Loan, Member, AuditLog
from app.services.credit_policy_v037 import evaluate_credit_policy

router = APIRouter(prefix="/admin/credit-policy", tags=["admin-credit-policy"])

class CreditPolicyIn(BaseModel):
    max_simultaneous_loans: int = Field(ge=0, le=100)
    max_installments: int = Field(ge=1, le=120)
    grace_days: int = Field(ge=0, le=90)
    min_on_time_ratio: Decimal | None = Field(default=None, ge=0, le=1)
    max_overdue_installments: int = Field(ge=0, le=100)
    max_installment_income_ratio: Decimal | None = Field(default=None, ge=0, le=1)
    max_quota_multiple: Decimal | None = Field(default=None, ge=0)

def _serialize(g):
    return {"group_id":g.id,"max_simultaneous_loans":g.max_simultaneous_loans,"max_installments":g.max_installments,"grace_days":g.grace_days,"min_on_time_ratio":str(g.min_on_time_ratio) if g.min_on_time_ratio is not None else None,"max_overdue_installments":g.max_overdue_installments,"max_installment_income_ratio":str(g.max_installment_income_ratio) if g.max_installment_income_ratio is not None else None,"max_quota_multiple":str(g.max_quota_multiple) if g.max_quota_multiple is not None else None}

@router.get("/{group_id}")
def get_policy(group_id:int, admin=Depends(require_admin), db:Session=Depends(get_db)):
    g=db.get(Group,group_id)
    if not g: raise HTTPException(404,"Grupo não encontrado.")
    return _serialize(g)

@router.put("/{group_id}")
def update_policy(group_id:int, data:CreditPolicyIn, admin=Depends(require_admin), db:Session=Depends(get_db)):
    g=db.get(Group,group_id)
    if not g: raise HTTPException(404,"Grupo não encontrado.")
    for k,v in data.model_dump().items(): setattr(g,k,v)
    db.add(AuditLog(actor_user_id=admin.id,action="CREDIT_POLICY_UPDATE",entity_type="GROUP",entity_id=str(g.id),details="credit policy updated"))
    db.commit(); db.refresh(g)
    return _serialize(g)

@router.get("/preview/{loan_id}")
def preview(loan_id:int, admin=Depends(require_admin), db:Session=Depends(get_db)):
    loan=db.get(Loan,loan_id)
    if not loan: raise HTTPException(404,"Empréstimo não encontrado.")
    member=db.get(Member,loan.member_id); g=db.get(Group,member.group_id) if member else None
    if not g: raise HTTPException(409,"Grupo não encontrado.")
    return {"loan_id":loan.id,"policy":evaluate_credit_policy(db,loan,g)}
