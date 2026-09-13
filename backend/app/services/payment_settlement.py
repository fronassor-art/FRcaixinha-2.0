"""Transactional, idempotent settlement of provider-confirmed PIX payments."""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Contribution, LedgerEntry, Loan, LoanInstallment, Member, Payment, PaymentSettlement
from app.services.ledger import post_contribution_payment
from app.services.loan_engine_v17 import ensure_loan_completion
from app.services.loan_payments_v17 import apply_confirmed_payment


CENT = Decimal("0.01")
ZERO = Decimal("0.00")
RECEIPT_VERSION = "v1"
_SECRET_KEYS = {"token", "access_token", "authorization", "secret", "password", "credential", "api_key", "private_key"}


def _money(value: Decimal | str | int | float | None) -> Decimal:
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def _is_postgresql(db: Session) -> bool:
    return db.bind is not None and db.bind.dialect.name == "postgresql"


def _lock_payment(db: Session, payment_id: int) -> Payment:
    query = db.query(Payment).filter(Payment.id == payment_id)
    if _is_postgresql(db):
        query = query.with_for_update()
    payment = query.one_or_none()
    if payment is None:
        raise ValueError("Pagamento não encontrado.")
    return payment


def _sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if str(key).lower() in _SECRET_KEYS else _sanitize_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_payload(item) for item in value]
    if isinstance(value, Decimal):
        return format(_money(value), "f")
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _persist_remote_payload(payment: Payment, remote_payload: dict[str, Any] | None) -> None:
    if not remote_payload:
        return
    safe_payload = _sanitize_payload(remote_payload)
    payment.provider_payload_json = _canonical_json(safe_payload)
    if not isinstance(safe_payload, dict):
        return
    external_reference = safe_payload.get("external_reference")
    status_detail = safe_payload.get("status_detail")
    if external_reference is not None:
        payment.external_reference = str(external_reference)
    if status_detail is not None:
        payment.provider_status_detail = str(status_detail)
    transaction_details = safe_payload.get("transaction_details")
    point_of_interaction = safe_payload.get("point_of_interaction")
    if not isinstance(transaction_details, dict) and isinstance(point_of_interaction, dict):
        transaction_details = point_of_interaction.get("transaction_data")
    if isinstance(transaction_details, dict):
        txid = transaction_details.get("txid")
        end_to_end_id = transaction_details.get("end_to_end_id")
        if txid is not None:
            payment.pix_txid = str(txid)
        if end_to_end_id is not None:
            payment.end_to_end_id = str(end_to_end_id)


def _contribution_paid_amount(contribution: Contribution) -> Decimal:
    if contribution.paid_amount is not None:
        return _money(contribution.paid_amount)
    return _money(contribution.amount) if contribution.status == "PAID" else ZERO


def contribution_financial_status(contribution: Contribution, paid_amount: Decimal, as_of: datetime) -> str:
    remaining = max(ZERO, _money(contribution.amount) - paid_amount)
    if remaining == ZERO:
        return "PAID"
    if contribution.due_date is not None and contribution.due_date < as_of.date():
        return "OVERDUE"
    if contribution.paid_amount is None and contribution.status in {"PENDING", "PARTIAL", "OVERDUE"}:
        return contribution.status
    return "PARTIAL" if paid_amount > ZERO else "PENDING"


def installment_financial_status(installment: LoanInstallment, as_of: datetime) -> str:
    remaining = _money(installment.amount) - _money(installment.paid_amount)
    remaining += max(ZERO, _money(installment.penalty_amount) - _money(installment.paid_penalty_amount))
    if remaining <= ZERO:
        return "PAID"
    if installment.due_date < as_of.date():
        return "OVERDUE"
    return "PARTIAL" if _money(installment.paid_amount) > ZERO or _money(installment.paid_penalty_amount) > ZERO else "PENDING"


def _locked_contribution(db: Session, payment: Payment) -> Contribution | None:
    contribution_id = None
    if (payment.reference_type or "").upper() == "CONTRIBUTION" and (payment.reference_id or "").isdigit():
        contribution_id = int(payment.reference_id)
    query = db.query(Contribution)
    if contribution_id is not None:
        query = query.filter(Contribution.id == contribution_id)
    else:
        query = query.filter(Contribution.payment_id == payment.id)
    if _is_postgresql(db):
        query = query.with_for_update()
    contribution = query.one_or_none()
    if contribution is not None and payment.reference_type and (payment.reference_type or "").upper() == "CONTRIBUTION":
        if contribution_id is None or contribution.id != contribution_id:
            raise ValueError("Referência de contribuição inválida.")
    return contribution


def _locked_installment(db: Session, payment: Payment) -> LoanInstallment | None:
    if (payment.reference_type or "").upper() != "LOAN_INSTALLMENT" or not (payment.reference_id or "").isdigit():
        return None
    query = db.query(LoanInstallment).filter(LoanInstallment.id == int(payment.reference_id))
    if _is_postgresql(db):
        query = query.with_for_update()
    return query.one_or_none()


def _ledger_snapshot(db: Session, payment_id: int) -> list[dict[str, Any]]:
    rows = db.query(LedgerEntry).filter(LedgerEntry.reference_id == str(payment_id)).order_by(LedgerEntry.id).all()
    return [
        {
            "id": row.id,
            "account": row.account,
            "direction": row.direction,
            "amount": format(_money(row.amount), "f"),
            "reference_type": row.reference_type,
            "reference_id": row.reference_id,
            "entry_hash": row.entry_hash,
        }
        for row in rows
    ]


def _receipt_snapshot(*, payment: Payment, settlement: PaymentSettlement, ledger: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "receipt_version": RECEIPT_VERSION,
        "receipt_number": settlement.receipt_number,
        "payment": {
            "id": payment.id,
            "provider": payment.provider,
            "provider_order_id": payment.provider_order_id,
            "provider_payment_id": payment.provider_payment_id,
            "external_reference": payment.external_reference,
            "pix_txid": payment.pix_txid,
            "end_to_end_id": payment.end_to_end_id,
            "reference_type": payment.reference_type,
            "reference_id": payment.reference_id,
        },
        "obligation": {
            "type": settlement.obligation_type,
            "contribution_id": settlement.contribution_id,
            "loan_installment_id": settlement.loan_installment_id,
            "member_id": settlement.member_id,
            "status_before": settlement.obligation_status_before,
            "status_after": settlement.obligation_status_after,
        },
        "amounts": {
            "received": format(_money(settlement.amount_received), "f"),
            "applied": format(_money(settlement.amount_applied), "f"),
            "principal": format(_money(settlement.principal_applied), "f"),
            "interest": format(_money(settlement.interest_applied), "f"),
            "penalty": format(_money(settlement.penalty_applied), "f"),
            "excess": format(_money(settlement.excess_amount), "f"),
        },
        "confirmation": {
            "source": settlement.confirmation_source,
            "confirmed_at": settlement.confirmed_at.astimezone(timezone.utc).isoformat(),
            "webhook_event_id": settlement.webhook_event_id,
        },
        "ledger_entries": ledger,
    }


def settle_confirmed_pix_payment(
    db: Session,
    payment: Payment,
    *,
    confirmation_source: str,
    webhook_event_id: int | None = None,
    remote_payload: dict[str, Any] | None = None,
    confirmed_at: datetime | None = None,
) -> PaymentSettlement:
    """Settle one confirmed PIX payment without committing the caller transaction."""
    if payment.id is None:
        raise ValueError("Pagamento precisa estar persistido antes da liquidação.")
    if not confirmation_source:
        raise ValueError("Origem da confirmação é obrigatória.")

    payment = _lock_payment(db, payment.id)
    existing = db.query(PaymentSettlement).filter(PaymentSettlement.payment_id == payment.id).one_or_none()
    if existing is not None:
        return existing
    if payment.ledger_posted_at is not None:
        raise ValueError("Pagamento legado já lançado não pode ser relançado sem recibo de liquidação.")

    effective_at = confirmed_at or payment.confirmed_at or datetime.now(timezone.utc)
    if effective_at.tzinfo is None:
        effective_at = effective_at.replace(tzinfo=timezone.utc)
    else:
        effective_at = effective_at.astimezone(timezone.utc)
    received = _money(payment.amount_received if payment.amount_received is not None else payment.amount)
    if received < ZERO:
        raise ValueError("Valor confirmado não pode ser negativo.")
    payment.amount_received = received
    if payment.confirmed_at is None:
        payment.confirmed_at = effective_at
    _persist_remote_payload(payment, remote_payload)

    contribution = _locked_contribution(db, payment)
    installment = _locked_installment(db, payment)
    if (contribution is None) == (installment is None):
        raise ValueError("Pagamento deve referenciar exatamente uma contribuição ou parcela de empréstimo.")

    penalty_applied = interest_applied = principal_applied = ZERO
    if contribution is not None:
        before_paid = _contribution_paid_amount(contribution)
        before_status = contribution_financial_status(contribution, before_paid, effective_at)
        open_amount = max(ZERO, _money(contribution.amount) - before_paid)
        applied = min(received, open_amount)
        principal_applied = applied
        after_paid = _money(before_paid + applied)
        contribution.paid_amount = after_paid
        after_status = contribution_financial_status(contribution, after_paid, effective_at)
        contribution.status = after_status
        if after_status == "PAID" and contribution.paid_at is None:
            contribution.paid_at = effective_at
        if applied > ZERO:
            post_contribution_payment(db, payment, amount=applied)
        obligation_type = "CONTRIBUTION"
        member_id = contribution.member_id
        contribution_id = contribution.id
        loan_installment_id = None
    else:
        assert installment is not None
        loan = db.get(Loan, installment.loan_id)
        if loan is None:
            raise ValueError("Empréstimo da parcela não encontrado.")
        member = db.get(Member, loan.member_id)
        if member is None:
            raise ValueError("Participante do empréstimo não encontrado.")
        before_penalty = _money(installment.paid_penalty_amount)
        before_base = _money(installment.paid_amount)
        before_status = installment_financial_status(installment, effective_at)
        interest_open = max(ZERO, _money(installment.interest) - min(_money(installment.interest), before_base))
        apply_confirmed_payment(db, payment, installment, amount=received)
        penalty_applied = _money(installment.paid_penalty_amount) - before_penalty
        base_applied = _money(installment.paid_amount) - before_base
        interest_applied = min(base_applied, interest_open)
        principal_applied = max(ZERO, base_applied - interest_applied)
        applied = _money(penalty_applied + base_applied)
        after_status = installment_financial_status(installment, effective_at)
        ensure_loan_completion(db, loan)
        obligation_type = "LOAN_INSTALLMENT"
        member_id = member.id
        contribution_id = None
        loan_installment_id = installment.id

    excess = _money(received - applied)
    payment.ledger_posted_at = datetime.now(timezone.utc)
    receipt_number = f"PIX-V1-{payment.id:012d}"
    settlement = PaymentSettlement(
        payment_id=payment.id,
        member_id=member_id,
        obligation_type=obligation_type,
        contribution_id=contribution_id,
        loan_installment_id=loan_installment_id,
        amount_received=received,
        amount_applied=applied,
        principal_applied=principal_applied,
        interest_applied=interest_applied,
        penalty_applied=penalty_applied,
        excess_amount=excess,
        obligation_status_before=before_status,
        obligation_status_after=after_status,
        confirmed_at=effective_at,
        confirmation_source=confirmation_source,
        webhook_event_id=webhook_event_id,
        receipt_number=receipt_number,
        receipt_version=RECEIPT_VERSION,
        receipt_snapshot_json="",
        receipt_hash="",
    )
    db.add(settlement)
    db.flush()
    snapshot = _receipt_snapshot(payment=payment, settlement=settlement, ledger=_ledger_snapshot(db, payment.id))
    canonical_snapshot = _canonical_json(snapshot)
    settlement.receipt_snapshot_json = canonical_snapshot
    settlement.receipt_hash = hashlib.sha256(canonical_snapshot.encode("utf-8")).hexdigest()
    db.flush()
    return settlement
