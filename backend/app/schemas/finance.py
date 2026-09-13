from datetime import date
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field
from app.core.loan_rules import MAX_LOAN_INSTALLMENTS

class ContributionIn(BaseModel):
    competence: date
    amount: Decimal = Field(gt=0)

class LoanSimulationIn(BaseModel):
    principal: Decimal = Field(gt=0)
    installments: int = Field(ge=1, le=MAX_LOAN_INSTALLMENTS)


class LoanSimulationConfirmationIn(BaseModel):
    simulation_token: str = Field(min_length=32, max_length=200)


class LoanRequestIn(LoanSimulationIn):
    """The official rate is server-owned and therefore is not request input."""
    model_config = ConfigDict(extra="forbid")
    simulation_token: str = Field(min_length=32, max_length=200)

class LoanDecisionIn(BaseModel):
    approve: bool
    force_exception: bool = False
    admin_note: str | None = Field(default=None, min_length=5, max_length=1000)

class LedgerReversalIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)
