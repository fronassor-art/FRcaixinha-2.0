"""Persistence helpers for approved annual cycle closings.

The preview remains read-only. Persisting an official snapshot is an explicit,
separate operation that reruns the typed engine from persisted evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    CycleAnnualClosing,
    CycleAnnualClosingSnapshot,
    CycleRealizedGainEvent,
    Payment,
    PaymentReversal,
    PaymentSettlement,
)
from app.models.core import CYCLE_EXTERNAL_GAIN_SOURCE_TYPES


SNAPSHOT_VERSION = "cycle_annual_closing_snapshot_v1"
CALCULATION_VERSION = "cycle_closing_v1"
CENT = Decimal("0.01")
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
GAIN_TYPES = frozenset({"INVESTMENT_YIELD_REALIZED", "OTHER_REALIZED_GAIN"})
# These are the only external source labels already used by the R2 flow.
# Unknown sources remain unsupported until an authoritative source registry exists.
EXTERNAL_GAIN_SOURCE_TYPES = frozenset(CYCLE_EXTERNAL_GAIN_SOURCE_TYPES)
INTERNAL_GAIN_SOURCE_TYPES = frozenset({
    "LOAN_INTEREST", "NORMAL_PRICE_INTEREST", "FIXED_PENALTY", "LATE_INTEREST",
    "LOAN_PRINCIPAL", "CONTRIBUTION", "PAYMENT_SETTLEMENT",
})


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _money(value: Any) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError("financial amounts must be Decimal")
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _validate_event_fields(
    *,
    event_type: str,
    amount: Decimal,
    realized_at: datetime,
    source_type: str,
    source_id: str,
    idempotency_key: str,
    evidence_reference: str,
    evidence_hash: str,
) -> tuple[Decimal, datetime]:
    if event_type not in GAIN_TYPES:
        raise ValueError("unsupported external realized gain type")
    if not isinstance(amount, Decimal) or amount <= 0:
        raise ValueError("gain amount must be a positive Decimal")
    if _money(amount) != amount:
        raise ValueError("gain amount must be expressed in cents")
    for label, value in (
        ("source_id", source_id),
        ("idempotency_key", idempotency_key),
        ("evidence_reference", evidence_reference),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} is required")
    if not isinstance(source_type, str) or (
        source_type in INTERNAL_GAIN_SOURCE_TYPES
        or source_type not in EXTERNAL_GAIN_SOURCE_TYPES
    ):
        raise ValueError("external gain source is unsupported or belongs to an internal source")
    if not isinstance(evidence_hash, str) or not HEX_SHA256.fullmatch(evidence_hash):
        raise ValueError("evidence_hash must be a lowercase SHA-256 hex digest")
    return amount, _utc(realized_at)


def _hash_text(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _identity_collides(candidate: str, identifiers: set[str]) -> bool:
    normalized_identifiers = {item.strip() for item in identifiers if item.strip()}
    return candidate.strip() in normalized_identifiers


def _reject_settlement_evidence_collision(
    db: Session, *, source_id: str, evidence_reference: str,
    idempotency_key: str, evidence_hash: str,
) -> None:
    """Fail closed when external evidence reuses persisted payment evidence."""
    identifiers: set[str] = set()
    hashes: set[str] = set()
    with db.no_autoflush:
        settlements = db.execute(select(PaymentSettlement)).scalars().all()
        for settlement in settlements:
            payment = db.get(Payment, settlement.payment_id)
            reversal = db.execute(
                select(PaymentReversal).where(PaymentReversal.settlement_id == settlement.id)
            ).scalar_one_or_none()
            identifiers.update({
                str(settlement.id), f"settlement:{settlement.id}",
                str(settlement.payment_id), f"payment:{settlement.payment_id}",
                str(settlement.receipt_number or ""), str(settlement.receipt_hash or ""),
            })
            components = {
                "CONTRIBUTION": ("CONTRIBUTION",),
                "LOAN_INSTALLMENT": ("LOAN_PRINCIPAL", "LOAN_INTEREST", "LOAN_PENALTY"),
                "AGREEMENT_INSTALLMENT": ("AGREEMENT",),
            }.get(settlement.obligation_type, ())
            for component in components:
                original_event_id = f"settlement:{settlement.id}:{component}:original"
                identifiers.add(original_event_id)
                derived_kinds = {
                    "LOAN_PENALTY": ("LOAN_FIXED_PENALTY", "LOAN_LATE_INTEREST"),
                    "AGREEMENT": ("AGREEMENT_PENALTY", "AGREEMENT_PRINCIPAL"),
                }.get(component, ())
                identifiers.update(f"{original_event_id}:{kind}" for kind in derived_kinds)
            hashes.update(filter(None, (
                settlement.receipt_hash,
                _hash_text(settlement.receipt_snapshot_json),
            )))
            if payment is not None:
                identifiers.update(filter(None, (
                    str(payment.id), f"payment:{payment.id}", payment.idempotency_key,
                    payment.reference_id, payment.provider_payment_id,
                    payment.provider_order_id, payment.external_reference,
                    payment.pix_txid, payment.end_to_end_id,
                )))
                if payment.reference_type and payment.reference_id:
                    identifiers.add(f"{payment.reference_type}:{payment.reference_id}")
                hashes.update(filter(None, (
                    payment.snapshot_hash,
                    _hash_text(payment.financial_snapshot_json),
                    _hash_text(payment.provider_payload_json),
                )))
            if reversal is not None:
                identifiers.update({
                    str(reversal.id), f"reversal:{reversal.id}",
                    str(reversal.receipt_number or ""), str(reversal.receipt_hash or ""),
                })
                hashes.update(filter(None, (
                    reversal.receipt_hash,
                    _hash_text(reversal.receipt_snapshot_json),
                )))
                for component in components:
                    reversal_event_id = (
                        f"settlement:{settlement.id}:{component}:reversal:{reversal.id}"
                    )
                    identifiers.add(reversal_event_id)
                    derived_kinds = {
                        "LOAN_PENALTY": ("LOAN_FIXED_PENALTY", "LOAN_LATE_INTEREST"),
                        "AGREEMENT": ("AGREEMENT_PENALTY", "AGREEMENT_PRINCIPAL"),
                    }.get(component, ())
                    identifiers.update(
                        f"{reversal_event_id}:{kind}" for kind in derived_kinds
                    )

    for value in (source_id, evidence_reference, idempotency_key):
        if _identity_collides(value, identifiers):
            raise ValueError("external gain evidence matches an existing payment settlement")
    if evidence_hash in hashes:
        raise ValueError("external gain evidence hash matches an existing payment settlement")


def create_or_get_cycle_annual_closing(
    db: Session, *, cycle_id: int, created_by: int | None = None,
) -> CycleAnnualClosing:
    existing = db.execute(
        select(CycleAnnualClosing).where(CycleAnnualClosing.cycle_id == cycle_id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = CycleAnnualClosing(cycle_id=cycle_id, created_by=created_by, updated_by=created_by)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        winner = db.execute(
            select(CycleAnnualClosing).where(CycleAnnualClosing.cycle_id == cycle_id)
        ).scalar_one_or_none()
        if winner is None:
            raise
        return winner
    return row


def record_realized_gain_event(
    db: Session,
    *,
    cycle_id: int,
    event_type: str,
    amount: Decimal,
    realized_at: datetime,
    source_type: str,
    source_id: str,
    idempotency_key: str,
    evidence_reference: str,
    evidence_hash: str,
    created_by: int | None = None,
) -> CycleRealizedGainEvent:
    amount, realized_at = _validate_event_fields(
        event_type=event_type,
        amount=amount,
        realized_at=realized_at,
        source_type=source_type,
        source_id=source_id,
        idempotency_key=idempotency_key,
        evidence_reference=evidence_reference,
        evidence_hash=evidence_hash,
    )
    _reject_settlement_evidence_collision(
        db, source_id=source_id, evidence_reference=evidence_reference,
        idempotency_key=idempotency_key, evidence_hash=evidence_hash,
    )
    row = CycleRealizedGainEvent(
        cycle_id=cycle_id,
        event_type=event_type,
        amount=amount,
        realized_at=realized_at,
        source_type=source_type,
        source_id=source_id.strip(),
        idempotency_key=idempotency_key.strip(),
        evidence_reference=evidence_reference.strip(),
        evidence_hash=evidence_hash,
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def reverse_realized_gain_event(
    db: Session,
    *,
    original_event_id: int,
    realized_at: datetime,
    source_type: str,
    source_id: str,
    idempotency_key: str,
    evidence_reference: str,
    evidence_hash: str,
    created_by: int | None = None,
) -> CycleRealizedGainEvent:
    original = db.get(CycleRealizedGainEvent, original_event_id)
    if original is None or original.reversal_of_id is not None:
        raise ValueError("original realized gain event not found")
    moment = _utc(realized_at)
    if moment < _utc(original.realized_at):
        raise ValueError("gain reversal cannot precede the original event")
    existing = db.execute(
        select(CycleRealizedGainEvent).where(
            CycleRealizedGainEvent.reversal_of_id == original.id
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ValueError("only one full reversal is supported for each gain event")
    return _record_full_reversal(
        db, original, moment, source_type, source_id, idempotency_key,
        evidence_reference, evidence_hash, created_by,
    )


def _record_full_reversal(
    db: Session,
    original: CycleRealizedGainEvent,
    moment: datetime,
    source_type: str,
    source_id: str,
    idempotency_key: str,
    evidence_reference: str,
    evidence_hash: str,
    created_by: int | None,
) -> CycleRealizedGainEvent:
    amount, moment = _validate_event_fields(
        event_type=original.event_type,
        amount=Decimal(original.amount),
        realized_at=moment,
        source_type=source_type,
        source_id=source_id,
        idempotency_key=idempotency_key,
        evidence_reference=evidence_reference,
        evidence_hash=evidence_hash,
    )
    _reject_settlement_evidence_collision(
        db, source_id=source_id, evidence_reference=evidence_reference,
        idempotency_key=idempotency_key, evidence_hash=evidence_hash,
    )
    row = CycleRealizedGainEvent(
        cycle_id=original.cycle_id,
        event_type=original.event_type,
        amount=amount,
        realized_at=moment,
        source_type=source_type,
        source_id=source_id.strip(),
        idempotency_key=idempotency_key.strip(),
        evidence_reference=evidence_reference.strip(),
        evidence_hash=evidence_hash,
        created_by=created_by,
        reversal_of_id=original.id,
    )
    db.add(row)
    db.flush()
    return row


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return _utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        raise TypeError("float is forbidden in canonical financial payloads")
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("canonical payload object keys must be strings")
        return {key: _canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported canonical payload value: {type(value).__name__}")


_ENGINE_KEYS = frozenset({
    "calculation_version", "cycle_id", "cycle_start_date", "closing_cutoff_at",
    "financial_timezone", "gross_realized_result", "administration_fee_rate",
    "administration_fee", "distributable_result", "total_eligible_contributions",
    "participants", "source_trace", "source_gaps", "unavailable_result_sources",
    "calculation_hash",
})
_PARTICIPANT_KEYS = frozenset({
    "member_id", "cycle_participation_id", "participation_status",
    "eligible_contributions", "rateio_eligible_contribution_principal",
    "refundable_contribution_principal", "weight", "gross_share",
    "gross_entitlement", "compensable_obligations", "compensable_obligations_total",
    "projected_compensation", "projected_net", "projected_residual_debt",
})
_OBLIGATION_KEYS = frozenset({
    "source_id", "kind", "amount", "due_date", "loan_id", "agreement_id",
})
_CONTRIBUTION_TRACE_KEYS = frozenset({
    "contribution_id", "member_id", "amount", "confirmed_net_at_cutoff",
    "eligible", "refundable_principal", "event_ids",
})
_GAIN_TRACE_KEYS = frozenset({"source_id", "source_type", "kind", "amount", "occurred_at"})


def _expect_keys(value: Any, expected: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{label} does not match the cycle_closing_v1 result schema")
    return value


def _expect_id(value: Any, label: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer identifier")


def _expect_text(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be non-empty text")


def _expect_decimal_text(value: Any, label: str, *, cents: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a Decimal string")
    try:
        result = Decimal(value)
    except Exception as exc:
        raise ValueError(f"{label} must be a Decimal string") from exc
    if not result.is_finite() or (
        cents and (
            result.quantize(CENT) != result
            or format(result, "f") != value
            or "." not in value
            or len(value.rsplit(".", 1)[1]) != 2
        )
    ):
        raise ValueError(f"{label} is not a finite supported Decimal")
    return result


def _validate_engine_result(value: Any) -> dict[str, Any]:
    result = _expect_keys(value, _ENGINE_KEYS, "engine result")
    if result["calculation_version"] != CALCULATION_VERSION:
        raise ValueError("unsupported closing calculation version")
    _expect_id(result["cycle_id"], "cycle_id")
    _expect_text(result["financial_timezone"], "financial_timezone")
    if result["financial_timezone"] != "America/Belem":
        raise ValueError("unsupported financial timezone")
    try:
        start = date.fromisoformat(result["cycle_start_date"])
        cutoff = datetime.fromisoformat(result["closing_cutoff_at"])
    except (TypeError, ValueError) as exc:
        raise ValueError("engine result has invalid cycle dates") from exc
    if start.isoformat() != result["cycle_start_date"] or cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("engine result dates must be canonical and cutoff timezone-aware")

    for name in (
        "gross_realized_result", "administration_fee_rate", "administration_fee",
        "distributable_result", "total_eligible_contributions",
    ):
        _expect_decimal_text(result[name], name, cents=name != "administration_fee_rate")

    if not isinstance(result["participants"], list):
        raise ValueError("participants must be a list")
    for participant in result["participants"]:
        row = _expect_keys(participant, _PARTICIPANT_KEYS, "participant")
        _expect_id(row["member_id"], "participant.member_id")
        _expect_id(row["cycle_participation_id"], "participant.cycle_participation_id")
        if row["participation_status"] not in {"ACTIVE", "BLOCKED_DELINQUENCY", "VOLUNTARILY_EXITED"}:
            raise ValueError("participant has unsupported status")
        for name in (
            "eligible_contributions", "rateio_eligible_contribution_principal",
            "refundable_contribution_principal", "gross_share", "gross_entitlement",
            "compensable_obligations_total", "projected_compensation", "projected_net",
            "projected_residual_debt",
        ):
            _expect_decimal_text(row[name], f"participant.{name}", cents=True)
        _expect_decimal_text(row["weight"], "participant.weight")
        if not isinstance(row["compensable_obligations"], list):
            raise ValueError("participant obligations must be a list")
        for obligation in row["compensable_obligations"]:
            item = _expect_keys(obligation, _OBLIGATION_KEYS, "obligation")
            _expect_text(item["source_id"], "obligation.source_id")
            _expect_text(item["kind"], "obligation.kind")
            _expect_decimal_text(item["amount"], "obligation.amount", cents=True)
            try:
                due = date.fromisoformat(item["due_date"])
            except (TypeError, ValueError) as exc:
                raise ValueError("obligation due_date must be an ISO date") from exc
            if due.isoformat() != item["due_date"]:
                raise ValueError("obligation due_date must be a canonical ISO date")
            _expect_id(item["loan_id"], "obligation.loan_id", nullable=True)
            _expect_id(item["agreement_id"], "obligation.agreement_id", nullable=True)

    trace = _expect_keys(
        result["source_trace"],
        frozenset({"contributions", "included_gains", "excluded_gains", "own_balance_settled_loan_ids"}),
        "source_trace",
    )
    for item in trace["contributions"]:
        row = _expect_keys(item, _CONTRIBUTION_TRACE_KEYS, "contribution trace")
        _expect_id(row["contribution_id"], "contribution_id")
        _expect_id(row["member_id"], "contribution.member_id")
        _expect_decimal_text(row["amount"], "contribution.amount", cents=True)
        _expect_decimal_text(row["confirmed_net_at_cutoff"], "confirmed_net_at_cutoff", cents=True)
        _expect_decimal_text(row["refundable_principal"], "refundable_principal", cents=True)
        if not isinstance(row["eligible"], bool) or not isinstance(row["event_ids"], list):
            raise ValueError("contribution trace has invalid typed fields")
        if any(not isinstance(event_id, str) or not event_id for event_id in row["event_ids"]):
            raise ValueError("contribution event identifiers must be non-empty text")
    for name in ("included_gains", "excluded_gains"):
        if not isinstance(trace[name], list):
            raise ValueError(f"{name} must be a list")
        for item in trace[name]:
            row = _expect_keys(item, _GAIN_TRACE_KEYS if name == "included_gains" else _GAIN_TRACE_KEYS | {"reason"}, name)
            for field in ("source_id", "source_type", "kind", "occurred_at"):
                _expect_text(row[field], f"{name}.{field}")
            _expect_decimal_text(row["amount"], f"{name}.amount", cents=True)
            try:
                moment = datetime.fromisoformat(row["occurred_at"].replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"{name}.occurred_at must be an ISO timestamp") from exc
            if moment.tzinfo is None or moment.utcoffset() is None:
                raise ValueError(f"{name}.occurred_at must be timezone-aware")
            if name == "excluded_gains":
                _expect_text(row["reason"], "excluded gain reason")
    if not isinstance(trace["own_balance_settled_loan_ids"], list):
        raise ValueError("own_balance_settled_loan_ids must be a list")
    for loan_id in trace["own_balance_settled_loan_ids"]:
        _expect_id(loan_id, "own balance loan_id")

    if not isinstance(result["source_gaps"], list):
        raise ValueError("source_gaps must be a list")
    for gap in result["source_gaps"]:
        row = _expect_keys(gap, frozenset({"code", "source"}), "source gap")
        _expect_text(row["code"], "source gap code")
        _expect_text(row["source"], "source gap source")
    if result["unavailable_result_sources"] != []:
        raise ValueError("cycle_closing_v1 cannot persist unavailable result sources")
    if not isinstance(result["calculation_hash"], str) or not HEX_SHA256.fullmatch(result["calculation_hash"]):
        raise ValueError("engine result calculation_hash must be a lowercase SHA-256 digest")
    memory = dict(result)
    expected_hash = memory.pop("calculation_hash")
    encoded_memory = json.dumps(memory, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if hashlib.sha256(encoded_memory.encode("utf-8")).hexdigest() != expected_hash:
        raise ValueError("engine result calculation_hash does not match its calculation memory")
    totals = _validate_result_totals(result, result["cycle_id"])
    if sum((Decimal(row["gross_share"]) for row in result["participants"]), Decimal("0.00")) != totals["distributable_result"]:
        raise ValueError("participant shares do not reconcile to distributable result")
    if sum((Decimal(row["eligible_contributions"]) for row in result["participants"]), Decimal("0.00")) != totals["total_eligible_contributions"]:
        raise ValueError("participant contributions do not reconcile to total eligible contributions")
    if sum((Decimal(row["amount"]) for row in trace["included_gains"]), Decimal("0.00")) != totals["gross_realized_result"]:
        raise ValueError("included gains do not reconcile to gross realized result")
    return result


def canonical_snapshot_payload(value: dict[str, Any]) -> str:
    _validate_engine_result(value)
    return json.dumps(
        _canonical_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def snapshot_payload_hash(canonical_payload: str) -> str:
    if not isinstance(canonical_payload, str):
        raise TypeError("canonical_payload must be text")
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


def _parse_canonical_payload(raw: str) -> dict[str, Any]:
    def reject_float(_value: str):
        raise ValueError("float is forbidden in canonical financial payloads")

    payload = json.loads(raw, parse_float=reject_float)
    if not isinstance(payload, dict) or canonical_snapshot_payload(payload) != raw:
        raise ValueError("snapshot payload is not canonical JSON")
    return payload


def _validate_result_totals(result: dict[str, Any], cycle_id: int) -> dict[str, Decimal]:
    if result.get("cycle_id") != cycle_id:
        raise ValueError("engine result Cycle does not match closing process")
    if result.get("calculation_version") != CALCULATION_VERSION:
        raise ValueError("unsupported closing calculation version")
    names = (
        "gross_realized_result",
        "administration_fee_rate",
        "administration_fee",
        "distributable_result",
        "total_eligible_contributions",
    )
    try:
        totals = {name: Decimal(result[name]) for name in names}
    except (KeyError, TypeError, ArithmeticError) as exc:
        raise ValueError("engine result has incomplete monetary memory") from exc
    gross = _money(totals["gross_realized_result"])
    fee = _money(totals["administration_fee"])
    distributable = _money(totals["distributable_result"])
    base = _money(totals["total_eligible_contributions"])
    rate = totals["administration_fee_rate"]
    if (
        gross < 0 or fee < 0 or distributable < 0 or base < 0
        or rate != Decimal("0.15")
        or fee != _money(gross * rate)
        or distributable != _money(gross - fee)
    ):
        raise ValueError("engine result totals are internally inconsistent")
    return {
        "gross_realized_result": gross,
        "administration_fee_rate": rate,
        "administration_fee": fee,
        "distributable_result": distributable,
        "total_eligible_contributions": base,
    }


def persist_approved_cycle_closing_snapshot(
    db: Session,
    *,
    closing_id: int,
    closing_cutoff_at: datetime,
    closed_by: int | None = None,
) -> CycleAnnualClosingSnapshot:
    from app.services.cycle_closing import preview_cycle_closing

    cutoff = _utc(closing_cutoff_at)
    closing = db.get(CycleAnnualClosing, closing_id)
    if closing is None:
        raise ValueError("annual closing process not found")
    if closing.status != "MASTER_APPROVED":
        raise ValueError("annual closing must be approved by Master before snapshot persistence")
    if db.execute(
        select(CycleAnnualClosingSnapshot.id).where(
            CycleAnnualClosingSnapshot.cycle_id == closing.cycle_id
        )
    ).scalar_one_or_none() is not None:
        raise ValueError("official snapshot already exists for Cycle")
    result = preview_cycle_closing(
        db, cycle_id=closing.cycle_id, closing_cutoff_at=cutoff
    )
    totals = _validate_result_totals(result, closing.cycle_id)
    payload = canonical_snapshot_payload(result)
    digest = snapshot_payload_hash(payload)
    snapshot = CycleAnnualClosingSnapshot(
        closing_id=closing.id,
        cycle_id=closing.cycle_id,
        snapshot_version=SNAPSHOT_VERSION,
        closing_cutoff_at=cutoff,
        calculation_version=CALCULATION_VERSION,
        canonical_payload=payload,
        payload_hash=digest,
        **totals,
        created_by=closed_by if closed_by is not None else closing.approved_by,
    )
    db.add(snapshot)
    db.flush()
    now = datetime.now(timezone.utc)
    closing.status = "CLOSED"
    closing.state_revision += 1
    closing.closed_at = now
    closing.closed_by = closed_by if closed_by is not None else closing.approved_by
    closing.updated_at = now
    closing.updated_by = closing.closed_by
    db.flush()
    return snapshot


def verify_cycle_annual_closing_snapshot(snapshot: CycleAnnualClosingSnapshot) -> bool:
    payload = _parse_canonical_payload(snapshot.canonical_payload)
    if snapshot.snapshot_version != SNAPSHOT_VERSION:
        raise ValueError("unsupported annual snapshot version")
    if payload.get("cycle_id") != snapshot.cycle_id:
        raise ValueError("snapshot Cycle does not match payload")
    if payload.get("calculation_version") != snapshot.calculation_version:
        raise ValueError("snapshot calculation version does not match payload")
    payload_cutoff = payload.get("closing_cutoff_at")
    if not isinstance(payload_cutoff, str):
        raise ValueError("snapshot payload has no cutoff")
    try:
        parsed_cutoff = datetime.fromisoformat(payload_cutoff.replace("Z", "+00:00"))
        if _utc(parsed_cutoff) != _utc(snapshot.closing_cutoff_at):
            raise ValueError("snapshot cutoff does not match payload")
    except (TypeError, ValueError) as exc:
        raise ValueError("snapshot cutoff is invalid or inconsistent") from exc
    if snapshot_payload_hash(snapshot.canonical_payload) != snapshot.payload_hash:
        raise ValueError("snapshot hash mismatch")
    totals = _validate_result_totals(payload, snapshot.cycle_id)
    for name, value in totals.items():
        if Decimal(getattr(snapshot, name)) != value:
            raise ValueError(f"snapshot relational total mismatch: {name}")
    return True
