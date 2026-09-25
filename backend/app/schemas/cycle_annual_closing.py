"""Small, explicit HTTP contracts for the annual closing administration flow."""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CashEvidenceIn(_Input):
    file_id: int = Field(gt=0)
    declared_cash_balance: Decimal
    observed_at: AwareDatetime
    closing_cutoff_at: AwareDatetime

    @field_validator("declared_cash_balance", mode="before")
    @classmethod
    def no_float_money(cls, value):
        if isinstance(value, bool) or isinstance(value, float):
            raise ValueError("money must be a decimal string")
        return value

    @field_validator("declared_cash_balance")
    @classmethod
    def cents(cls, value: Decimal) -> Decimal:
        try:
            if not value.is_finite() or value < 0 or value != value.quantize(Decimal("0.01")):
                raise ValueError("declared_cash_balance must be nonnegative finite cents")
        except InvalidOperation as exc:
            raise ValueError("declared_cash_balance must be nonnegative finite cents") from exc
        return value


class CashEvidenceOut(BaseModel):
    id: int
    closing_id: int
    cycle_id: int
    file_id: int
    declared_cash_balance: str
    observed_at: datetime
    closing_cutoff_at: datetime
    uploaded_by: int
    attested_by: int
    attested_at: datetime


class PrepareReviewIn(_Input):
    expected_state_revision: int = Field(ge=0)
    closing_cutoff_at: AwareDatetime
    cash_evidence_id: int = Field(gt=0)


class ReviewOut(BaseModel):
    review_id: int
    closing_id: int
    cycle_id: int
    review_version: int
    process_revision: int
    closing_cutoff_at: datetime
    calculation_version: str
    calculation_hash: str
    reconciliation_hash: str
    cash_evidence_id: int
    ledger_cash_balance: str
    actual_cash_balance: str
    reconciliation_difference: str
    participant_payout_liability: str
    administration_fee: str
    required_liquidity: str
    liquidity_surplus: str
    created_at: datetime


class ApproveReviewIn(_Input):
    expected_state_revision: int = Field(ge=0)


class CloseIn(_Input):
    expected_state_revision: int = Field(ge=0)
    closing_cutoff_at: AwareDatetime


class SnapshotOut(BaseModel):
    snapshot_id: int
    closing_id: int
    cycle_id: int
    status: str
    state_revision: int
    closing_cutoff_at: datetime
    snapshot_version: str
    calculation_version: str
    payload_hash: str
    created_at: datetime
    created_by: int | None


class AnnualClosingStateOut(BaseModel):
    closing_id: int
    cycle_id: int
    status: str
    state_revision: int
    approved_at: datetime | None
    approved_by: int | None
    approved_review_id: int | None
    latest_review: ReviewOut | None


class PreviewGapOut(BaseModel):
    code: str
    source: str


class AnnualPreviewOut(BaseModel):
    cycle_id: int
    closing_cutoff_at: datetime
    calculation_version: str
    calculation_hash: str
    gross_realized_result: str
    administration_fee: str
    distributable_result: str
    total_eligible_contributions: str
    source_gaps: list[PreviewGapOut]
    unavailable_result_sources: list[PreviewGapOut]
