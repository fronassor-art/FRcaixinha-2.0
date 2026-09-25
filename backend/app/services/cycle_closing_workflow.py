"""Annual closing review and Master approval over persisted financial evidence."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.models import (AuditLog, CycleAnnualClosing, CycleAnnualClosingCashEvidence,
                        CycleAnnualClosingReview, LedgerEntry, User,
                        WorkflowExecutionEvidenceFile)
from app.services.cycle_closing import FINANCIAL_PREVIEW_MODELS, preview_cycle_closing
from app.services.ledger import _hash_payload


CENT = Decimal("0.01")
ZERO = Decimal("0.00")
REVIEW_SCHEMA = "cycle_annual_closing_review_v1"
RECONCILIATION_SCHEMA = "cycle_annual_reconciliation_v1"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CASH_REFERENCE_TYPES = frozenset({
    "CONTRIBUTION_PAYMENT", "EXPENSE", "LOAN_DISBURSEMENT",
    "LOAN_INSTALLMENT_PAYMENT", "LOAN_INTEREST_PAYMENT",
    "LOAN_PENALTY_PAYMENT", "AGREEMENT_INSTALLMENT_PAYMENT", "REVERSAL",
})


class ClosingWorkflowConflict(ValueError):
    """The requested state or revision lost a concurrent claim."""


class StaleClosingReview(ValueError):
    """The approved financial review no longer matches persisted evidence."""


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("closing cutoff must be timezone-aware")
    return value.astimezone(timezone.utc)


def _stored_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("ledger timestamp is missing")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _money(value: Decimal, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value != value.quantize(CENT):
        raise ValueError(f"{name} must be a finite Decimal amount in cents")
    return value.quantize(CENT)


def _canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ledger_cash_at_cutoff(db: Session, cutoff: datetime) -> tuple[Decimal, list[dict]]:
    """Validate the global hash-chain prefix and total only CAIXINHA movements."""
    with db.no_autoflush:
        rows = db.execute(select(LedgerEntry).order_by(LedgerEntry.id)).scalars().all()
    cutoff_rows = [row for row in rows if _stored_utc(row.created_at) <= cutoff]
    if not cutoff_rows:
        prefix = []
    else:
        max_id = cutoff_rows[-1].id
        prefix = [row for row in rows if row.id <= max_id]
        if any(_stored_utc(row.created_at) > cutoff for row in prefix):
            raise ValueError("ledger ordering cannot establish an unambiguous cutoff prefix")
    previous_hash = None
    previous_id = 0
    for row in prefix:
        if row.id <= previous_id or not row.entry_hash:
            raise ValueError("ledger chain sequence or hash is missing")
        expected_hash = _hash_payload(
            SimpleNamespace(
                account=row.account, direction=row.direction, amount=row.amount,
                reference_type=row.reference_type, reference_id=row.reference_id,
                reversal_of_id=row.reversal_of_id, created_at=_stored_utc(row.created_at),
            ), previous_hash,
        )
        if row.previous_hash != previous_hash or row.entry_hash != expected_hash:
            raise ValueError("ledger integrity cannot prove annual cash position")
        previous_hash = row.entry_hash
        previous_id = row.id
    by_id = {row.id: row for row in prefix}
    selected = [row for row in prefix if _stored_utc(row.created_at) <= cutoff]
    reversal_ids: set[int] = set()
    balance = ZERO
    trace = []
    for row in selected:
        amount = _money(Decimal(row.amount), "ledger amount")
        if amount <= ZERO or row.direction not in {"CREDIT", "DEBIT"} or not row.account:
            raise ValueError(f"ledger entry {row.id} cannot prove cash position")
        if row.reference_type in CASH_REFERENCE_TYPES and row.account != "CAIXINHA":
            raise ValueError(f"cash movement {row.id} is posted outside CAIXINHA")
        if row.reversal_of_id is not None:
            if row.reference_type != "REVERSAL":
                raise ValueError(f"ledger reversal {row.id} has an invalid reference type")
            original = by_id.get(row.reversal_of_id)
            if (
                original is None or original.reversal_of_id is not None
                or original.id in reversal_ids
                or _stored_utc(original.created_at) > _stored_utc(row.created_at)
                or original.account != row.account
                or original.direction == row.direction
                or _money(Decimal(original.amount), "reversed ledger amount") != amount
            ):
                raise ValueError(f"ledger reversal {row.id} cannot prove cash position")
            reversal_ids.add(original.id)
        elif row.reference_type == "REVERSAL":
            raise ValueError(f"ledger reversal {row.id} has no original entry")
        if row.account == "CAIXINHA":
            balance += amount if row.direction == "CREDIT" else -amount
        trace.append({
            "id": row.id, "account": row.account, "direction": row.direction,
            "amount": str(amount), "reference_type": row.reference_type,
            "reference_id": row.reference_id, "reversal_of_id": row.reversal_of_id,
            "created_at": _iso(_stored_utc(row.created_at)),
            "entry_hash": row.entry_hash,
        })
    return balance.quantize(CENT), trace


def record_cycle_annual_closing_cash_evidence(
    db: Session, *, closing_id: int, file_id: int, declared_cash_balance: Decimal,
    observed_at: datetime, closing_cutoff_at: datetime, attested_by: int,
) -> CycleAnnualClosingCashEvidence:
    """Bind a Master attestation to a stored file after rehashing its bytes."""
    closing = db.get(CycleAnnualClosing, closing_id)
    if closing is None:
        raise ValueError("annual closing process not found")
    master = _actor(db, attested_by, master=True)
    observed, cutoff = _utc(observed_at), _utc(closing_cutoff_at)
    if observed != cutoff:
        raise ValueError("cash observation instant must equal the closing cutoff")
    balance = _money(declared_cash_balance, "declared_cash_balance")
    if balance < ZERO:
        raise ValueError("declared cash balance must be nonnegative")
    file_row = db.get(WorkflowExecutionEvidenceFile, file_id)
    if (file_row is None or file_row.revoked_at is not None
            or not isinstance(file_row.sha256, str) or not SHA256.fullmatch(file_row.sha256)):
        raise ValueError("stored cash evidence file is missing or revoked")
    from app.services.workflow_evidence_integrity_v069 import verify_file
    verification = verify_file(db, file_row.id, master.id)
    if verification.status != "PASS" or verification.observed_sha256 != file_row.sha256:
        raise ValueError("stored cash evidence file hash verification failed")
    existing = db.execute(select(CycleAnnualClosingCashEvidence).where(
        CycleAnnualClosingCashEvidence.closing_id == closing.id,
        CycleAnnualClosingCashEvidence.file_id == file_row.id,
        CycleAnnualClosingCashEvidence.file_sha256 == verification.observed_sha256,
        CycleAnnualClosingCashEvidence.declared_cash_balance == balance,
        CycleAnnualClosingCashEvidence.observed_at == observed,
        CycleAnnualClosingCashEvidence.closing_cutoff_at == cutoff,
        CycleAnnualClosingCashEvidence.attested_by == master.id,
    )).scalar_one_or_none()
    if existing is not None:
        return existing
    evidence = CycleAnnualClosingCashEvidence(
        closing_id=closing.id, cycle_id=closing.cycle_id, file_id=file_row.id,
        storage_reference=file_row.storage_key, file_sha256=verification.observed_sha256,
        declared_cash_balance=balance, observed_at=observed, closing_cutoff_at=cutoff,
        uploaded_by=file_row.uploaded_by, attested_by=master.id,
        attested_at=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc),
    )
    db.add(evidence)
    db.flush()
    return evidence


def _validate_cash_evidence(db: Session, *, evidence_id: int, closing_id: int,
                            cycle_id: int, cutoff: datetime) -> CycleAnnualClosingCashEvidence:
    evidence = db.get(CycleAnnualClosingCashEvidence, evidence_id)
    if evidence is None or evidence.closing_id != closing_id or evidence.cycle_id != cycle_id:
        raise ValueError("cash evidence does not belong to this annual closing")
    if _stored_utc(evidence.observed_at) != cutoff or _stored_utc(evidence.closing_cutoff_at) != cutoff:
        raise ValueError("cash observation does not match the closing cutoff")
    master = _actor(db, evidence.attested_by, master=True)
    file_row = db.get(WorkflowExecutionEvidenceFile, evidence.file_id)
    if (file_row is None or file_row.revoked_at is not None
            or file_row.storage_key != evidence.storage_reference
            or file_row.sha256 != evidence.file_sha256):
        raise ValueError("cash evidence file link is stale or revoked")
    from app.services.workflow_evidence_integrity_v069 import verify_file
    verification = verify_file(db, file_row.id, master.id)
    if verification.status != "PASS" or verification.observed_sha256 != evidence.file_sha256:
        raise ValueError("cash evidence file integrity verification failed")
    return evidence


def _evidence(
    db: Session, *, cycle_id: int, cutoff: datetime, actual_cash_balance: Decimal,
    cash_evidence_reference: str, cash_evidence_hash: str,
    cash_evidence_id: int, cash_evidence_sha256: str,
    cash_evidence_observed_at: datetime, cash_evidence_attested_by: int,
    cash_evidence_file_id: int,
) -> tuple[dict, dict, str, str]:
    actual = _money(actual_cash_balance, "actual_cash_balance")
    if actual < ZERO:
        raise ValueError("actual_cash_balance must be nonnegative")
    if not isinstance(cash_evidence_reference, str) or not cash_evidence_reference.strip():
        raise ValueError("cash_evidence_reference is required")
    if not isinstance(cash_evidence_hash, str) or not SHA256.fullmatch(cash_evidence_hash):
        raise ValueError("cash_evidence_hash must be lowercase SHA-256")
    result = preview_cycle_closing(db, cycle_id=cycle_id, closing_cutoff_at=cutoff)
    if result["source_gaps"] or result["unavailable_result_sources"]:
        raise ValueError("annual closing preview has blocking source gaps")
    ledger_balance, ledger_trace = _ledger_cash_at_cutoff(db, cutoff)
    difference = (actual - ledger_balance).quantize(CENT)
    if difference != ZERO:
        raise ValueError("annual closing cash reconciliation differs from zero")
    payout = sum(
        (_money(Decimal(item["projected_net"]), "projected_net") for item in result["participants"]),
        ZERO,
    ).quantize(CENT)
    fee = _money(Decimal(result["administration_fee"]), "administration_fee")
    required = (payout + fee).quantize(CENT)
    surplus = (actual - required).quantize(CENT)
    if surplus < ZERO:
        raise ValueError("annual closing has insufficient reconciled liquidity")
    reconciliation = {
        "schema": RECONCILIATION_SCHEMA,
        "cycle_id": cycle_id,
        "cutoff": _iso(cutoff),
        "calculation_hash": result["calculation_hash"],
        "ledger_cash_balance": str(ledger_balance),
        "actual_cash_balance": str(actual),
        "reconciliation_difference": str(difference),
        "participant_payout_liability": str(payout),
        "administration_fee": str(fee),
        "required_liquidity": str(required),
        "liquidity_surplus": str(surplus),
        "cash_evidence_reference": cash_evidence_reference.strip(),
        "cash_evidence_hash": cash_evidence_hash,
        "cash_evidence_id": cash_evidence_id,
        "cash_evidence_file_sha256": cash_evidence_sha256,
        "cash_evidence_observed_at": _iso(cash_evidence_observed_at),
        "cash_evidence_attested_by": cash_evidence_attested_by,
        "cash_evidence_file_id": cash_evidence_file_id,
        "ledger_entries": ledger_trace,
    }
    raw = _canonical(reconciliation)
    return result, reconciliation, raw, _digest(raw)


def _review_memory(review: CycleAnnualClosingReview) -> dict:
    return {
        "schema": REVIEW_SCHEMA,
        "closing_id": review.closing_id, "cycle_id": review.cycle_id,
        "review_version": review.review_version, "process_revision": review.process_revision,
        "cutoff": _iso(_stored_utc(review.closing_cutoff_at)),
        "calculation_version": review.calculation_version,
        "calculation_hash": review.calculation_hash,
        "ledger_cash_balance": str(review.ledger_cash_balance),
        "actual_cash_balance": str(review.actual_cash_balance),
        "reconciliation_difference": str(review.reconciliation_difference),
        "participant_payout_liability": str(review.participant_payout_liability),
        "administration_fee": str(review.administration_fee),
        "required_liquidity": str(review.required_liquidity),
        "liquidity_surplus": str(review.liquidity_surplus),
        "cash_evidence_reference": review.cash_evidence_reference,
        "cash_evidence_hash": review.cash_evidence_hash,
        "cash_evidence_id": review.cash_evidence_id,
        "cash_evidence_file_sha256": review.cash_evidence_hash,
        "reconciliation_hash": review.reconciliation_hash,
    }


def verify_cycle_annual_closing_review_read_only(
    review: CycleAnnualClosingReview,
) -> bool:
    """Verify stored review/reconciliation hashes using pure in-memory work only."""
    try:
        if not SHA256.fullmatch(review.review_hash or ""):
            return False
        if not SHA256.fullmatch(review.reconciliation_hash or ""):
            return False
        raw = review.reconciliation_payload
        if not isinstance(raw, str) or _digest(raw) != review.reconciliation_hash:
            return False

        def reject_float(_value: str):
            raise ValueError("float is forbidden in persisted reconciliation")

        memory = json.loads(raw, parse_float=reject_float)
        if not isinstance(memory, dict) or _canonical(memory) != raw:
            return False
        expected_memory = {
            "schema": RECONCILIATION_SCHEMA,
            "cycle_id": review.cycle_id,
            "cutoff": _iso(_stored_utc(review.closing_cutoff_at)),
            "calculation_hash": review.calculation_hash,
            "ledger_cash_balance": str(review.ledger_cash_balance),
            "actual_cash_balance": str(review.actual_cash_balance),
            "reconciliation_difference": str(review.reconciliation_difference),
            "participant_payout_liability": str(review.participant_payout_liability),
            "administration_fee": str(review.administration_fee),
            "required_liquidity": str(review.required_liquidity),
            "liquidity_surplus": str(review.liquidity_surplus),
            "cash_evidence_reference": review.cash_evidence_reference,
            "cash_evidence_hash": review.cash_evidence_hash,
            "cash_evidence_id": review.cash_evidence_id,
            "cash_evidence_file_sha256": review.cash_evidence_hash,
        }
        if any(memory.get(key) != value for key, value in expected_memory.items()):
            return False
        return _digest(_canonical(_review_memory(review))) == review.review_hash
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def _audit(db: Session, *, actor_id: int, closing: CycleAnnualClosing,
           review: CycleAnnualClosingReview, from_status: str, to_status: str,
           revision_before: int, revision_after: int) -> None:
    details = {
        "closing_id": closing.id, "cycle_id": closing.cycle_id,
        "review_id": review.id, "review_version": review.review_version,
        "from_status": from_status, "to_status": to_status,
        "revision_before": revision_before, "revision_after": revision_after,
        "cutoff": _iso(_stored_utc(review.closing_cutoff_at)),
        "calculation_hash": review.calculation_hash,
        "reconciliation_hash": review.reconciliation_hash,
        "cash_evidence_hash": review.cash_evidence_hash,
        "actor": actor_id,
    }
    db.add(AuditLog(
        actor_user_id=actor_id, action="CYCLE_ANNUAL_CLOSING_REVIEW" if to_status == "READY_FOR_REVIEW"
        else "CYCLE_ANNUAL_CLOSING_APPROVED" if to_status == "MASTER_APPROVED"
        else "CYCLE_ANNUAL_CLOSING_CLOSED",
        entity_type="CYCLE_ANNUAL_CLOSING", entity_id=str(closing.id), details=_canonical(details),
    ))


def _actor(db: Session, actor_id: int, *, master: bool) -> User:
    with db.no_autoflush:
        query = db.query(User).filter(User.id == actor_id)
        if master and db.bind is not None and db.bind.dialect.name == "postgresql":
            query = query.with_for_update().populate_existing()
        actor = query.one_or_none()
    if actor is None or not actor.is_active:
        raise ValueError("active closing actor not found")
    if master and (actor.role != "ADMIN" or not actor.is_master):
        raise ValueError("only the active Master administrator may approve an annual closing")
    return actor


def _lock_closing_financial_sources(
    db: Session, *, closing_id: int, status: str, revision: int,
) -> None:
    """Keep source writers out until the caller commits the reviewed transition."""
    dialect = db.bind.dialect.name if db.bind is not None else None
    if dialect == "postgresql":
        claimed = db.execute(text(
            "SELECT id FROM cycle_annual_closings "
            "WHERE id=:id AND status=:status AND state_revision=:revision FOR UPDATE"
        ), {"id": closing_id, "status": status, "revision": revision}).scalar_one_or_none()
        if claimed is None:
            raise ClosingWorkflowConflict("stale annual closing state revision")
        table_names = sorted({model.__table__.name for model in FINANCIAL_PREVIEW_MODELS})
        table_names.extend(name for name in (
            "cycle_annual_closing_cash_evidence", "workflow_execution_evidence_files",
            LedgerEntry.__table__.name,
        ) if name not in table_names)
        table_names.sort()
        db.execute(text("LOCK TABLE " + ", ".join(table_names) + " IN SHARE MODE"))
    elif dialect == "sqlite":
        claimed = db.execute(text(
            "UPDATE cycle_annual_closings SET state_revision=state_revision "
            "WHERE id=:id AND status=:status AND state_revision=:revision"
        ), {"id": closing_id, "status": status, "revision": revision})
        if claimed.rowcount != 1:
            raise ClosingWorkflowConflict("stale annual closing state revision")
    else:
        raise RuntimeError("unsupported database dialect for annual closing locks")


def _latest_review(db: Session, closing_id: int) -> CycleAnnualClosingReview | None:
    return db.execute(
        select(CycleAnnualClosingReview)
        .where(CycleAnnualClosingReview.closing_id == closing_id)
        .order_by(CycleAnnualClosingReview.review_version.desc())
        .limit(1)
    ).scalar_one_or_none()


def prepare_cycle_closing_review(
    db: Session, *, closing_id: int, expected_state_revision: int,
    closing_cutoff_at: datetime, cash_evidence_id: int, actor_id: int,
) -> CycleAnnualClosingReview:
    _actor(db, actor_id, master=False)
    cutoff = _utc(closing_cutoff_at)
    closing = db.get(CycleAnnualClosing, closing_id)
    if closing is None:
        raise ValueError("annual closing process not found")
    if closing.state_revision != expected_state_revision:
        raise ClosingWorkflowConflict("stale annual closing state revision")
    if closing.status not in {"ASSESSING", "READY_FOR_REVIEW"}:
        raise ClosingWorkflowConflict("annual closing cannot enter review from current status")
    if closing.approved_review_id is not None:
        raise ClosingWorkflowConflict("approved annual closing cannot be reviewed again")
    _lock_closing_financial_sources(
        db, closing_id=closing_id, status=closing.status,
        revision=expected_state_revision,
    )
    cash = _validate_cash_evidence(
        db, evidence_id=cash_evidence_id, closing_id=closing.id,
        cycle_id=closing.cycle_id, cutoff=cutoff,
    )
    result, reconciliation, raw, recon_hash = _evidence(
        db, cycle_id=closing.cycle_id, cutoff=cutoff,
        actual_cash_balance=Decimal(cash.declared_cash_balance),
        cash_evidence_reference=cash.storage_reference,
        cash_evidence_hash=cash.file_sha256,
        cash_evidence_id=cash.id, cash_evidence_sha256=cash.file_sha256,
        cash_evidence_observed_at=_stored_utc(cash.observed_at),
        cash_evidence_attested_by=cash.attested_by, cash_evidence_file_id=cash.file_id,
    )
    latest = _latest_review(db, closing.id)
    if closing.status == "READY_FOR_REVIEW" and latest is None:
        raise ClosingWorkflowConflict("ready annual closing has no financial review")
    if closing.status == "READY_FOR_REVIEW" and latest is not None and (
        latest.process_revision == closing.state_revision
        and latest.reconciliation_hash == recon_hash
        and latest.calculation_hash == result["calculation_hash"]
        and _stored_utc(latest.closing_cutoff_at) == cutoff
        and latest.cash_evidence_hash == cash.file_sha256
        and latest.cash_evidence_id == cash.id
    ):
        return latest
    version = latest.review_version + 1 if latest is not None else 1
    revision_after = expected_state_revision + 1
    review = CycleAnnualClosingReview(
        closing_id=closing.id, cycle_id=closing.cycle_id,
        cash_evidence_id=cash.id,
        review_version=version, process_revision=revision_after,
        closing_cutoff_at=cutoff,
        calculation_version=result["calculation_version"],
        calculation_hash=result["calculation_hash"],
        ledger_cash_balance=Decimal(reconciliation["ledger_cash_balance"]),
        actual_cash_balance=Decimal(reconciliation["actual_cash_balance"]),
        reconciliation_difference=Decimal(reconciliation["reconciliation_difference"]),
        participant_payout_liability=Decimal(reconciliation["participant_payout_liability"]),
        administration_fee=Decimal(reconciliation["administration_fee"]),
        required_liquidity=Decimal(reconciliation["required_liquidity"]),
        liquidity_surplus=Decimal(reconciliation["liquidity_surplus"]),
        cash_evidence_reference=cash.storage_reference,
        cash_evidence_hash=cash.file_sha256,
        reconciliation_payload=raw, reconciliation_hash=recon_hash,
        created_by=actor_id,
    )
    review.review_hash = _digest(_canonical(_review_memory(review)))
    now = datetime.now(timezone.utc)
    with db.begin_nested():
        claimed = db.execute(
            update(CycleAnnualClosing)
            .where(
                CycleAnnualClosing.id == closing_id,
                CycleAnnualClosing.status == closing.status,
                CycleAnnualClosing.state_revision == expected_state_revision,
            )
            .values(status="READY_FOR_REVIEW", state_revision=revision_after,
                    updated_at=now, updated_by=actor_id)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            raise ClosingWorkflowConflict("annual closing review was claimed concurrently")
        db.add(review)
        db.flush()
        _audit(db, actor_id=actor_id, closing=closing, review=review,
               from_status=closing.status, to_status="READY_FOR_REVIEW",
               revision_before=expected_state_revision, revision_after=revision_after)
        db.flush()
    db.refresh(closing)
    return review


def revalidate_cycle_closing_review(db: Session, review: CycleAnnualClosingReview) -> dict:
    """Return the current preview only when every approved review field still matches."""
    if _digest(_canonical(_review_memory(review))) != review.review_hash:
        raise StaleClosingReview("annual closing review hash mismatch")
    cutoff = _stored_utc(review.closing_cutoff_at)
    try:
        cash = _validate_cash_evidence(
            db, evidence_id=review.cash_evidence_id, closing_id=review.closing_id,
            cycle_id=review.cycle_id, cutoff=cutoff,
        )
        result, reconciliation, raw, recon_hash = _evidence(
            db, cycle_id=review.cycle_id, cutoff=cutoff,
            actual_cash_balance=Decimal(cash.declared_cash_balance),
            cash_evidence_reference=cash.storage_reference,
            cash_evidence_hash=cash.file_sha256,
            cash_evidence_id=cash.id, cash_evidence_sha256=cash.file_sha256,
            cash_evidence_observed_at=_stored_utc(cash.observed_at),
            cash_evidence_attested_by=cash.attested_by, cash_evidence_file_id=cash.file_id,
        )
    except (ValueError, TypeError) as exc:
        raise StaleClosingReview("annual closing review evidence is stale") from exc
    fields = (
        "ledger_cash_balance", "actual_cash_balance", "reconciliation_difference",
        "participant_payout_liability", "administration_fee", "required_liquidity",
        "liquidity_surplus", "cash_evidence_reference", "cash_evidence_hash",
    )
    if (
        result["calculation_version"] != review.calculation_version
        or result["calculation_hash"] != review.calculation_hash
        or recon_hash != review.reconciliation_hash
        or raw != review.reconciliation_payload
        or any(str(getattr(review, field)) != reconciliation[field] for field in fields)
    ):
        raise StaleClosingReview("annual closing review no longer matches current evidence")
    return result


def approve_cycle_closing_review(
    db: Session, *, closing_id: int, review_id: int,
    expected_state_revision: int, actor_id: int,
) -> CycleAnnualClosing:
    _actor(db, actor_id, master=True)
    closing = db.get(CycleAnnualClosing, closing_id)
    if closing is None:
        raise ValueError("annual closing process not found")
    if closing.state_revision != expected_state_revision or closing.status != "READY_FOR_REVIEW":
        raise ClosingWorkflowConflict("stale or invalid annual closing approval")
    _lock_closing_financial_sources(
        db, closing_id=closing_id, status="READY_FOR_REVIEW",
        revision=expected_state_revision,
    )
    review = db.get(CycleAnnualClosingReview, review_id)
    latest = _latest_review(db, closing_id)
    if (
        review is None or latest is None or review.id != latest.id
        or review.closing_id != closing.id or review.cycle_id != closing.cycle_id
        or review.process_revision != expected_state_revision
    ):
        raise ClosingWorkflowConflict("annual closing review is not current")
    revalidate_cycle_closing_review(db, review)
    now = datetime.now(timezone.utc)
    with db.begin_nested():
        claimed = db.execute(
            update(CycleAnnualClosing)
            .where(
                CycleAnnualClosing.id == closing_id,
                CycleAnnualClosing.status == "READY_FOR_REVIEW",
                CycleAnnualClosing.state_revision == expected_state_revision,
                CycleAnnualClosing.approved_review_id.is_(None),
            )
            .values(status="MASTER_APPROVED", state_revision=expected_state_revision + 1,
                    approved_at=now, approved_by=actor_id, approved_review_id=review.id,
                    updated_at=now, updated_by=actor_id)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            raise ClosingWorkflowConflict("annual closing approval was claimed concurrently")
        _audit(db, actor_id=actor_id, closing=closing, review=review,
               from_status="READY_FOR_REVIEW", to_status="MASTER_APPROVED",
               revision_before=expected_state_revision, revision_after=expected_state_revision + 1)
        db.flush()
    db.refresh(closing)
    return closing


def audit_closed_cycle_closing(db: Session, *, closing: CycleAnnualClosing,
                               review: CycleAnnualClosingReview, actor_id: int,
                               revision_before: int) -> None:
    _audit(db, actor_id=actor_id, closing=closing, review=review,
           from_status="MASTER_APPROVED", to_status="CLOSED",
           revision_before=revision_before, revision_after=revision_before + 1)
