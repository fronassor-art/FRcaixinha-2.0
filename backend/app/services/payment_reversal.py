"""Atomic internal reversal of provider-confirmed Contribution payments."""

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    Contribution,
    LedgerEntry,
    Payment,
    PaymentReversal,
    PaymentReversalComponent,
    PaymentSettlement,
    User,
)
from app.services.ledger import post_entry
from app.services.payment_settlement import _is_postgresql, _money, contribution_financial_status


ZERO = Decimal("0.00")
REVERSAL_RECEIPT_VERSION = "v1"


def _utc_now(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("now precisa ser timezone-aware")
    return result.astimezone(timezone.utc)


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _lock_payment(db: Session, payment_id: int) -> Payment:
    with db.no_autoflush:
        query = db.query(Payment).filter(Payment.id == payment_id)
        if _is_postgresql(db):
            query = query.with_for_update()
        payment = query.one_or_none()
    if payment is None:
        raise ValueError("Pagamento não encontrado.")
    return payment


def _lock_settlement(db: Session, payment_id: int) -> PaymentSettlement | None:
    with db.no_autoflush:
        query = db.query(PaymentSettlement).filter(PaymentSettlement.payment_id == payment_id)
        if _is_postgresql(db):
            query = query.with_for_update()
        return query.one_or_none()


def _lock_contribution(db: Session, contribution_id: int) -> Contribution | None:
    with db.no_autoflush:
        query = db.query(Contribution).filter(Contribution.id == contribution_id)
        if _is_postgresql(db):
            query = query.with_for_update()
        return query.one_or_none()


def _validate_admin(db: Session, admin_id: int) -> User:
    admin = db.get(User, admin_id)
    if admin is None or not admin.is_active or admin.role != "ADMIN" or not admin.is_master:
        raise ValueError("Somente Administrador Master pode estornar pagamentos.")
    return admin


def _validate_money(settlement: PaymentSettlement, payment: Payment) -> dict[str, Decimal]:
    values = {
        "amount_received": _money(settlement.amount_received),
        "amount_applied": _money(settlement.amount_applied),
        "principal_applied": _money(settlement.principal_applied),
        "interest_applied": _money(settlement.interest_applied),
        "penalty_applied": _money(settlement.penalty_applied),
        "excess_amount": _money(settlement.excess_amount),
    }
    if any(value < ZERO for value in values.values()):
        raise ValueError("Settlement possui valores financeiros negativos.")
    if values["amount_received"] != values["amount_applied"] + values["excess_amount"]:
        raise ValueError("Settlement possui equação de recebimento inválida.")
    if values["amount_applied"] != values["principal_applied"] + values["interest_applied"] + values["penalty_applied"]:
        raise ValueError("Settlement possui equação de aplicação inválida.")
    expected_received = _money(payment.amount_received if payment.amount_received is not None else payment.amount)
    if values["amount_received"] != expected_received:
        raise ValueError("Settlement não corresponde ao valor recebido pelo pagamento.")
    if values["interest_applied"] != ZERO or values["penalty_applied"] != ZERO:
        raise ValueError("Contribution não pode possuir juros ou multa aplicados.")
    if values["principal_applied"] != values["amount_applied"]:
        raise ValueError("Settlement de Contribution possui principal incompatível.")
    return values


def _validate_contribution_reference(
    payment: Payment, settlement: PaymentSettlement, contribution: Contribution
) -> None:
    reference_type = (payment.reference_type or "").strip().upper()
    if reference_type == "CONTRIBUTION":
        if payment.reference_id != str(contribution.id):
            raise ValueError("Referência de contribuição incompatível.")
    elif reference_type:
        raise ValueError("Pagamento não referencia uma contribuição.")
    elif contribution.payment_id != payment.id:
        raise ValueError("Pagamento não está vinculado à contribuição.")
    if settlement.payment_id != payment.id or settlement.contribution_id != contribution.id:
        raise ValueError("Settlement não corresponde ao pagamento/contribuição.")
    if settlement.member_id != contribution.member_id:
        raise ValueError("Settlement não corresponde ao membro da contribuição.")
    if settlement.obligation_type != "CONTRIBUTION" or settlement.receipt_version != "v1":
        raise ValueError("Settlement incompatível com reversão de Contribution.")
    if settlement.loan_installment_id is not None or settlement.agreement_installment_id is not None:
        raise ValueError("Settlement possui obrigação incompatível.")
    if any(
        value is not None
        for value in (
            settlement.loan_status_before,
            settlement.loan_status_after,
            settlement.loan_state_revision_before,
            settlement.loan_state_revision_after,
        )
    ):
        raise ValueError("Settlement de Contribution não pode possuir evidência de Loan.")


def _find_original_ledger(db: Session, payment_id: int, amount: Decimal) -> list[LedgerEntry]:
    with db.no_autoflush:
        query = db.query(LedgerEntry).filter(LedgerEntry.reference_id == str(payment_id)).order_by(LedgerEntry.id)
        if _is_postgresql(db):
            query = query.with_for_update()
        rows = query.all()
    originals = [row for row in rows if row.reference_type == "CONTRIBUTION_PAYMENT"]
    if amount == ZERO:
        if rows:
            raise ValueError("Ledger inesperado para settlement sem valor aplicado.")
        return []
    if len(originals) != 1:
        raise ValueError("Ledger de confirmação ausente ou duplicado.")
    row = originals[0]
    extras = [extra for extra in rows if extra.id != row.id]
    if any(extra.reversal_of_id == row.id for extra in extras):
        raise ValueError("Ledger de confirmação já possui reversão.")
    if extras:
        raise ValueError("Ledger possui componentes inesperados.")
    if row.account != "CAIXINHA" or row.direction != "CREDIT":
        raise ValueError("Ledger de confirmação incompatível.")
    if _money(row.amount) != amount or row.reversal_of_id is not None:
        raise ValueError("Valor ou origem do Ledger de confirmação inválido.")
    with db.no_autoflush:
        generic = db.query(LedgerEntry).filter(LedgerEntry.reversal_of_id == row.id).first()
    if generic is not None:
        raise ValueError("Ledger de confirmação já possui reversão.")
    return rows


def _receipt_snapshot(
    *,
    reversal: PaymentReversal,
    payment: Payment,
    settlement: PaymentSettlement,
    contribution: Contribution,
    state_before: str,
    state_after: str,
    components: list[PaymentReversalComponent],
) -> dict[str, Any]:
    return {
        "receipt_version": reversal.receipt_version,
        "receipt_number": reversal.receipt_number,
        "reversal": {
            "id": reversal.id,
            "reversed_at": reversal.reversed_at.astimezone(timezone.utc).isoformat(),
            "reversal_competence": reversal.reversal_competence.isoformat(),
            "admin_id": reversal.admin_id,
            "reason": reversal.reason,
        },
        "payment": {"id": payment.id, "status": payment.status},
        "settlement": {
            "id": settlement.id,
            "receipt_number": settlement.receipt_number,
            "receipt_version": settlement.receipt_version,
        },
        "obligation": {
            "type": settlement.obligation_type,
            "contribution_id": contribution.id,
            "original_competence": contribution.competence.isoformat(),
            "state_before": state_before,
            "state_after": state_after,
        },
        "amounts": {
            "amount_received": format(_money(reversal.amount_received), "f"),
            "amount_applied": format(_money(reversal.amount_applied), "f"),
            "principal_applied": format(_money(reversal.principal_applied), "f"),
            "interest_applied": format(_money(reversal.interest_applied), "f"),
            "penalty_applied": format(_money(reversal.penalty_applied), "f"),
            "excess_amount": format(_money(reversal.excess_amount), "f"),
        },
        "ledger_components": [
            {
                "original_ledger_entry_id": component.original_ledger_entry_id,
                "compensating_ledger_entry_id": component.compensating_ledger_entry_id,
                "account": component.original_ledger_entry.account,
                "amount": format(_money(component.original_ledger_entry.amount), "f"),
                "original_direction": component.original_ledger_entry.direction,
                "compensating_direction": component.compensating_ledger_entry.direction,
            }
            for component in sorted(components, key=lambda item: item.original_ledger_entry_id)
        ],
    }


def reverse_payment(
    db: Session,
    *,
    payment_id: int,
    admin_id: int,
    reason: str,
    now: datetime | None = None,
) -> PaymentReversal:
    """Reverse one Contribution payment, without committing the transaction."""
    normalized_reason = reason.strip() if isinstance(reason, str) else ""
    if len(normalized_reason) < 5:
        raise ValueError("Informe um motivo de reversão com pelo menos 5 caracteres.")

    reversed_at = _utc_now(now)
    payment = _lock_payment(db, payment_id)
    with db.no_autoflush:
        existing = db.query(PaymentReversal).filter(PaymentReversal.payment_id == payment.id).one_or_none()
    if existing is not None:
        return existing

    if payment.status != "approved":
        raise ValueError("Somente pagamentos aprovados podem ser estornados.")
    _validate_admin(db, admin_id)
    settlement = _lock_settlement(db, payment.id)
    if settlement is None:
        raise ValueError("PaymentSettlement obrigatório não encontrado.")
    if settlement.contribution_id is None:
        raise ValueError("Settlement não referencia Contribution.")
    contribution = _lock_contribution(db, settlement.contribution_id)
    if contribution is None:
        raise ValueError("Contribution não encontrada.")
    _validate_contribution_reference(payment, settlement, contribution)
    values = _validate_money(settlement, payment)
    if values["amount_applied"] > _money(contribution.amount):
        raise ValueError("Settlement excede o valor da Contribution.")

    current_paid = _money(contribution.paid_amount)
    if current_paid < values["amount_applied"]:
        raise ValueError("Contribution não possui valor pago suficiente para o estorno.")
    original_status = contribution.status
    new_paid = current_paid - values["amount_applied"]
    after_status = contribution_financial_status(contribution, new_paid, reversed_at)
    originals = _find_original_ledger(db, payment.id, values["amount_applied"])

    reversal = PaymentReversal(
        payment_id=payment.id,
        settlement_id=settlement.id,
        admin_id=admin_id,
        reason=normalized_reason,
        reversed_at=reversed_at,
        reversal_competence=date(reversed_at.year, reversed_at.month, 1),
        original_competence=contribution.competence,
        original_due_date=None,
        original_date_kind="CONTRIBUTION_COMPETENCE",
        amount_received=values["amount_received"],
        amount_applied=values["amount_applied"],
        principal_applied=values["principal_applied"],
        interest_applied=values["interest_applied"],
        penalty_applied=values["penalty_applied"],
        excess_amount=values["excess_amount"],
        receipt_number=f"PIX-REV-V1-{payment.id}",
        receipt_version=REVERSAL_RECEIPT_VERSION,
        receipt_snapshot_json=f"pending:{payment.id}",
        receipt_hash=hashlib.sha256(f"pending:{payment.id}".encode("utf-8")).hexdigest(),
    )
    db.add(reversal)
    db.flush()

    components: list[PaymentReversalComponent] = []
    for original in originals:
        compensating = post_entry(
            db,
            original.account,
            "DEBIT" if original.direction == "CREDIT" else "CREDIT",
            _money(original.amount),
            "REVERSAL",
            str(original.id),
            reversal_of_id=original.id,
        )
        db.flush()
        if compensating.id == original.id or compensating.reversal_of_id != original.id:
            raise ValueError("Compensação de Ledger incompatível com o original.")
        component = PaymentReversalComponent(
            payment_reversal_id=reversal.id,
            original_ledger_entry_id=original.id,
            compensating_ledger_entry_id=compensating.id,
        )
        db.add(component)
        components.append(component)
    db.flush()

    contribution.paid_amount = new_paid
    contribution.status = after_status
    contribution.paid_at = contribution.paid_at if after_status == "PAID" else None

    snapshot = _receipt_snapshot(
        reversal=reversal,
        payment=payment,
        settlement=settlement,
        contribution=contribution,
        state_before=original_status,
        state_after=after_status,
        components=components,
    )
    reversal.receipt_snapshot_json = _canonical_json(snapshot)
    reversal.receipt_hash = hashlib.sha256(reversal.receipt_snapshot_json.encode("utf-8")).hexdigest()

    details = {
        "payment_id": payment.id,
        "reversal_id": reversal.id,
        "settlement_id": settlement.id,
        "contribution_id": contribution.id,
        "reason": normalized_reason,
        "reversal_competence": reversal.reversal_competence.isoformat(),
        "amounts": snapshot["amounts"],
        "receipt_number": reversal.receipt_number,
        "receipt_hash": reversal.receipt_hash,
    }
    db.add(
        AuditLog(
            actor_user_id=admin_id,
            action="PAYMENT_REVERSED",
            entity_type="PAYMENT",
            entity_id=str(payment.id),
            details=_canonical_json(details),
        )
    )
    db.flush()
    return reversal
