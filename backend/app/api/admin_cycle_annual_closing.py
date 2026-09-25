"""Administrative HTTP surface for the approved annual closing workflow."""

from decimal import Decimal
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import AwareDatetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_master
from app.db.session import get_db
from app.models import (
    Cycle, CycleAnnualClosing, CycleAnnualClosingReview, WorkflowExecutionEvidenceFile,
)
from app.schemas.cycle_annual_closing import (
    AnnualClosingStateOut, AnnualPreviewOut, ApproveReviewIn, CashEvidenceIn,
    CashEvidenceOut, CloseIn, PrepareReviewIn, ReviewOut, SnapshotOut,
)
from app.services.cycle_closing import ClosingContractGap, preview_cycle_closing
from app.services.cycle_closing_persistence import (
    create_or_get_cycle_annual_closing, persist_approved_cycle_closing_snapshot,
)
from app.services.cycle_closing_workflow import (
    ClosingWorkflowConflict, StaleClosingReview, approve_cycle_closing_review,
    prepare_cycle_closing_review, record_cycle_annual_closing_cash_evidence,
)


router = APIRouter(prefix="/admin/finance", tags=["admin-finance"])
PositiveId = Annotated[int, Path(gt=0)]

# These are the actual service gates that can surface as plain ValueError.
# Unrecognized exceptions retain their original type and traceback.
_FINANCIAL_GATES = frozenset({
    "cash evidence does not belong to this annual closing",
    "cash observation does not match the closing cutoff",
    "cash evidence file link is stale or revoked",
    "cash evidence file integrity verification failed",
    "annual closing preview has blocking source gaps",
    "annual closing cash reconciliation differs from zero",
    "annual closing has insufficient reconciled liquidity",
    "snapshot cutoff differs from approved annual closing review",
    "annual closing must be approved by Master before snapshot persistence",
    "annual closing has no valid approved review",
    "annual closing has no audited Master approval",
    "official snapshot already exists for Cycle",
    "ledger timestamp is missing",
    "ledger ordering cannot establish an unambiguous cutoff prefix",
    "ledger chain sequence or hash is missing",
    "ledger integrity cannot prove annual cash position",
    "stored cash evidence file is missing or revoked",
    "stored cash evidence file hash verification failed",
    "unsupported annual snapshot version",
    "snapshot Cycle does not match payload",
    "snapshot calculation version does not match payload",
    "snapshot payload has no cutoff",
    "snapshot cutoff does not match payload",
    "snapshot cutoff is invalid or inconsistent",
    "snapshot hash mismatch",
    "engine result totals are internally inconsistent",
})
_FINANCIAL_ROW_GATES = (
    re.compile(r"ledger entry [1-9][0-9]* cannot prove cash position"),
    re.compile(r"cash movement [1-9][0-9]* is posted outside CAIXINHA"),
    re.compile(r"ledger reversal [1-9][0-9]* (?:has an invalid reference type|cannot prove cash position|has no original entry)"),
    re.compile(r"snapshot relational total mismatch: [a-z_]+"),
)


def _money(value: Decimal | str) -> str:
    return f"{Decimal(value):.2f}"


def _review_out(row: CycleAnnualClosingReview) -> ReviewOut:
    return ReviewOut(
        review_id=row.id, closing_id=row.closing_id, cycle_id=row.cycle_id,
        review_version=row.review_version, process_revision=row.process_revision,
        closing_cutoff_at=row.closing_cutoff_at,
        calculation_version=row.calculation_version, calculation_hash=row.calculation_hash,
        reconciliation_hash=row.reconciliation_hash, cash_evidence_id=row.cash_evidence_id,
        ledger_cash_balance=_money(row.ledger_cash_balance),
        actual_cash_balance=_money(row.actual_cash_balance),
        reconciliation_difference=_money(row.reconciliation_difference),
        participant_payout_liability=_money(row.participant_payout_liability),
        administration_fee=_money(row.administration_fee),
        required_liquidity=_money(row.required_liquidity),
        liquidity_surplus=_money(row.liquidity_surplus), created_at=row.created_at,
    )


def _state_out(db: Session, closing: CycleAnnualClosing) -> AnnualClosingStateOut:
    latest = db.execute(
        select(CycleAnnualClosingReview)
        .where(CycleAnnualClosingReview.closing_id == closing.id)
        .order_by(CycleAnnualClosingReview.review_version.desc()).limit(1)
    ).scalar_one_or_none()
    return AnnualClosingStateOut(
        closing_id=closing.id, cycle_id=closing.cycle_id, status=closing.status,
        state_revision=closing.state_revision, approved_at=closing.approved_at,
        approved_by=closing.approved_by, approved_review_id=closing.approved_review_id,
        latest_review=_review_out(latest) if latest is not None else None,
    )


def _closing_or_404(db: Session, closing_id: int) -> CycleAnnualClosing:
    closing = db.get(CycleAnnualClosing, closing_id)
    if closing is None:
        raise HTTPException(404, "annual closing process not found")
    return closing


def _translate(exc: ValueError, operation: str) -> HTTPException | None:
    if isinstance(exc, (ClosingWorkflowConflict, StaleClosingReview, ClosingContractGap)):
        return HTTPException(409, str(exc))
    message = str(exc)
    if message in {"active closing actor not found", "only the active Master administrator may approve an annual closing"}:
        return HTTPException(403, message)
    if operation == "cash" and message == "cash observation instant must equal the closing cutoff":
        return HTTPException(400, message)
    if message in _FINANCIAL_GATES or any(pattern.fullmatch(message) for pattern in _FINANCIAL_ROW_GATES):
        return HTTPException(409, message)
    return None


def _rollback_error(db: Session, exc: ValueError, operation: str):
    db.rollback()
    translated = _translate(exc, operation)
    if translated is None:
        raise exc
    raise translated from exc


@router.get("/cycles/{cycle_id}/annual-closing/preview", response_model=AnnualPreviewOut)
def annual_preview(
    cycle_id: PositiveId,
    closing_cutoff_at: Annotated[AwareDatetime, Query()],
    admin=Depends(require_admin), db: Session = Depends(get_db),
):
    if db.get(Cycle, cycle_id) is None:
        raise HTTPException(404, "cycle not found")
    try:
        preview = preview_cycle_closing(db, cycle_id=cycle_id, closing_cutoff_at=closing_cutoff_at)
    except ClosingContractGap as exc:
        raise HTTPException(409, str(exc)) from exc
    return AnnualPreviewOut(
        cycle_id=preview["cycle_id"], closing_cutoff_at=closing_cutoff_at,
        calculation_version=preview["calculation_version"],
        calculation_hash=preview["calculation_hash"],
        gross_realized_result=_money(preview["gross_realized_result"]),
        administration_fee=_money(preview["administration_fee"]),
        distributable_result=_money(preview["distributable_result"]),
        total_eligible_contributions=_money(preview["total_eligible_contributions"]),
        source_gaps=preview["source_gaps"],
        unavailable_result_sources=preview["unavailable_result_sources"],
    )


@router.get("/cycles/{cycle_id}/annual-closing", response_model=AnnualClosingStateOut)
def annual_state(cycle_id: PositiveId, admin=Depends(require_admin), db: Session = Depends(get_db)):
    closing = db.execute(
        select(CycleAnnualClosing).where(CycleAnnualClosing.cycle_id == cycle_id)
    ).scalar_one_or_none()
    if closing is None:
        raise HTTPException(404, "annual closing process not found")
    return _state_out(db, closing)


@router.post("/cycles/{cycle_id}/annual-closing/cash-evidence", response_model=CashEvidenceOut)
def attest_cash_evidence(
    cycle_id: PositiveId, body: CashEvidenceIn,
    master=Depends(require_master), db: Session = Depends(get_db),
):
    try:
        if db.get(Cycle, cycle_id) is None:
            raise HTTPException(404, "cycle not found")
        if db.get(WorkflowExecutionEvidenceFile, body.file_id) is None:
            raise HTTPException(404, "stored cash evidence file not found")
        closing = create_or_get_cycle_annual_closing(db, cycle_id=cycle_id, created_by=master.id)
        evidence = record_cycle_annual_closing_cash_evidence(
            db, closing_id=closing.id, file_id=body.file_id,
            declared_cash_balance=body.declared_cash_balance,
            observed_at=body.observed_at, closing_cutoff_at=body.closing_cutoff_at,
            attested_by=master.id,
        )
        result = CashEvidenceOut(
            id=evidence.id, closing_id=evidence.closing_id, cycle_id=evidence.cycle_id,
            file_id=evidence.file_id, declared_cash_balance=_money(evidence.declared_cash_balance),
            observed_at=evidence.observed_at, closing_cutoff_at=evidence.closing_cutoff_at,
            uploaded_by=evidence.uploaded_by, attested_by=evidence.attested_by,
            attested_at=evidence.attested_at,
        )
        db.commit()
        return result
    except HTTPException:
        db.rollback()
        raise
    except ValueError as exc:
        _rollback_error(db, exc, "cash")
    except Exception:
        db.rollback()
        raise


@router.post("/annual-closings/{closing_id}/reviews", response_model=ReviewOut)
def prepare_review(
    closing_id: PositiveId, body: PrepareReviewIn,
    admin=Depends(require_admin), db: Session = Depends(get_db),
):
    try:
        _closing_or_404(db, closing_id)
        review = prepare_cycle_closing_review(
            db, closing_id=closing_id,
            expected_state_revision=body.expected_state_revision,
            closing_cutoff_at=body.closing_cutoff_at,
            cash_evidence_id=body.cash_evidence_id, actor_id=admin.id,
        )
        result = _review_out(review)
        db.commit()
        return result
    except HTTPException:
        db.rollback()
        raise
    except ValueError as exc:
        _rollback_error(db, exc, "prepare")
    except Exception:
        db.rollback()
        raise


@router.post("/annual-closings/{closing_id}/reviews/{review_id}/approve", response_model=AnnualClosingStateOut)
def approve_review(
    closing_id: PositiveId, review_id: PositiveId, body: ApproveReviewIn,
    master=Depends(require_master), db: Session = Depends(get_db),
):
    try:
        _closing_or_404(db, closing_id)
        if db.get(CycleAnnualClosingReview, review_id) is None:
            raise HTTPException(404, "annual closing review not found")
        closing = approve_cycle_closing_review(
            db, closing_id=closing_id, review_id=review_id,
            expected_state_revision=body.expected_state_revision, actor_id=master.id,
        )
        result = _state_out(db, closing)
        db.commit()
        return result
    except HTTPException:
        db.rollback()
        raise
    except ValueError as exc:
        _rollback_error(db, exc, "approve")
    except Exception:
        db.rollback()
        raise


@router.post("/annual-closings/{closing_id}/close", response_model=SnapshotOut)
def close_annual_cycle(
    closing_id: PositiveId, body: CloseIn,
    master=Depends(require_master), db: Session = Depends(get_db),
):
    try:
        _closing_or_404(db, closing_id)
        snapshot = persist_approved_cycle_closing_snapshot(
            db, closing_id=closing_id, closing_cutoff_at=body.closing_cutoff_at,
            closed_by=master.id, expected_state_revision=body.expected_state_revision,
        )
        closing = _closing_or_404(db, closing_id)
        result = SnapshotOut(
            snapshot_id=snapshot.id, closing_id=snapshot.closing_id, cycle_id=snapshot.cycle_id,
            status=closing.status, state_revision=closing.state_revision,
            closing_cutoff_at=snapshot.closing_cutoff_at,
            snapshot_version=snapshot.snapshot_version,
            calculation_version=snapshot.calculation_version,
            payload_hash=snapshot.payload_hash, created_at=snapshot.created_at,
            created_by=snapshot.created_by,
        )
        db.commit()
        return result
    except HTTPException:
        db.rollback()
        raise
    except ValueError as exc:
        _rollback_error(db, exc, "close")
    except Exception:
        db.rollback()
        raise
