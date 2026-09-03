from datetime import date
from decimal import Decimal
from pydantic import BaseModel, Field

class ContributionIn(BaseModel):
    competence: date
    amount: Decimal = Field(gt=0)

class LoanRequestIn(BaseModel):
    principal: Decimal = Field(gt=0)
    monthly_rate: Decimal = Field(ge=0, le=1)
    installments: int = Field(ge=1, le=24)

class LoanDecisionIn(BaseModel):
    approve: bool
    force_exception: bool = False
    admin_note: str | None = Field(default=None, min_length=5, max_length=1000)

class LedgerReversalIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)
