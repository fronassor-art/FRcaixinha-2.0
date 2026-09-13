from datetime import date
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field
from app.core.loan_rules import MAX_LOAN_INSTALLMENTS

class LoanCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    installments: int = Field(ge=1, le=MAX_LOAN_INSTALLMENTS)
    first_due_date: date
    purpose: Optional[str] = Field(default=None, max_length=500)

class LoanDecision(BaseModel):
    approved: bool
    monthly_rate_percent: Optional[Decimal] = Field(default=None, ge=0)
    admin_note: Optional[str] = Field(default=None, max_length=1000)
