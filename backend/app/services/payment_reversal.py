"""Atomic internal reversal of provider-confirmed Contribution payments."""

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.loan_rules import (
    LATE_CHARGE_SETTLEMENT_COMPONENT_VERSION,
    LATE_CHARGE_VERSION,
)
from app.models import (
    AuditLog,
    AgreementInstallment,
    CollectionAgreement,
    Contribution,
    LedgerEntry,
    Loan,
    LoanInstallment,
    MemberFinancialEntry,
    Payment,
    PaymentReversal,
    PaymentReversalComponent,
    PaymentSettlement,
    User,
)
from app.services.ledger import post_entry
from app.services.loan_engine_v17 import lock_loan, touch_loan
from app.services.loan_obligation_runtime import materialize_reversal_late_interest_adjustment
from app.services.agreements_v039 import lock_collection_agreement, touch_collection_agreement
from app.services.member_financial import add_member_financial_entry, get_member_financial_position, lock_member_financial_account
from app.services.payment_settlement import _is_postgresql, _ledger_snapshot, _money, _receipt_snapshot as _settlement_receipt_snapshot, contribution_financial_status


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


def _active_contribution_credit(
    db: Session,
    account_id: int,
    contribution_id: int,
) -> Decimal:
    """Return the net current patrimonial credit for one Contribution."""
    with db.no_autoflush:
        entries = (
            db.query(MemberFinancialEntry)
            .filter(
                MemberFinancialEntry.account_id == account_id,
                MemberFinancialEntry.contribution_id == contribution_id,
            )
            .order_by(MemberFinancialEntry.id)
            .all()
        )
    credits = sum(
        (_money(entry.amount) for entry in entries
         if entry.entry_type == "CONTRIBUTION" and entry.direction == "CREDIT"),
        ZERO,
    )
    reversals = sum(
        (_money(entry.amount) for entry in entries
         if entry.entry_type == "CONTRIBUTION_REVERSAL" and entry.direction == "DEBIT"),
        ZERO,
    )
    return _money(credits - reversals)


def _validate_admin(db: Session, admin_id: int) -> User:
    admin = db.get(User, admin_id)
    if admin is None or not admin.is_active or admin.role != "ADMIN" or not admin.is_master:
        raise ValueError("Somente Administrador Master pode estornar pagamentos.")
    return admin


def _validate_money(settlement: PaymentSettlement, payment: Payment, *, loan: bool = False) -> dict[str, Decimal]:
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
    if settlement.settlement_component_version is not None:
        if (
            settlement.settlement_component_version
            != LATE_CHARGE_SETTLEMENT_COMPONENT_VERSION
        ):
            raise ValueError("Versão de componentes do settlement inválida.")
        if any(
            value is None
            for value in (
                settlement.normal_interest_applied,
                settlement.late_interest_applied,
                settlement.fixed_penalty_applied,
            )
        ):
            raise ValueError("Settlement versionado possui componente ausente.")
        values.update(
            {
                "normal_interest_applied": _money(settlement.normal_interest_applied),
                "late_interest_applied": _money(settlement.late_interest_applied),
                "fixed_penalty_applied": _money(settlement.fixed_penalty_applied),
            }
        )
        if values["interest_applied"] != values["normal_interest_applied"]:
            raise ValueError("Settlement possui juros normais incompatíveis.")
        if values["penalty_applied"] != values["late_interest_applied"] + values["fixed_penalty_applied"]:
            raise ValueError("Settlement possui encargos de mora incompatíveis.")
    expected_received = _money(payment.amount_received if payment.amount_received is not None else payment.amount)
    if values["amount_received"] != expected_received:
        raise ValueError("Settlement não corresponde ao valor recebido pelo pagamento.")
    if not loan and (values["interest_applied"] != ZERO or values["penalty_applied"] != ZERO):
        raise ValueError("Contribution não pode possuir juros ou multa aplicados.")
    if not loan and values["principal_applied"] != values["amount_applied"]:
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


def _lock_installment(db: Session, installment_id: int) -> LoanInstallment | None:
    with db.no_autoflush:
        query = db.query(LoanInstallment).filter(LoanInstallment.id == installment_id)
        if _is_postgresql(db):
            query = query.with_for_update().populate_existing()
        return query.one_or_none()


def _same_timestamp(left: datetime | None, right: datetime | None) -> bool:
    def normalize(value):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return normalize(left) == normalize(right)


def _timestamp_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _loan_ledgers(db: Session, payment_id: int, values: dict[str, Decimal]) -> list[LedgerEntry]:
    with db.no_autoflush:
        query = db.query(LedgerEntry).filter(LedgerEntry.reference_id == str(payment_id)).order_by(LedgerEntry.id)
        if _is_postgresql(db):
            query = query.with_for_update()
        rows = query.all()
    result = []
    for kind, amount in (("LOAN_INTEREST_PAYMENT", values["interest_applied"]), ("LOAN_PENALTY_PAYMENT", values["penalty_applied"])):
        matches = [row for row in rows if row.reference_type == kind]
        if amount > ZERO:
            if len(matches) != 1:
                raise ValueError("Ledger de componente ausente ou duplicado.")
            row = matches[0]
            if row.account != "CAIXINHA" or row.direction != "CREDIT" or _money(row.amount) != amount or row.reversal_of_id is not None:
                raise ValueError("Ledger de componente incompatível.")
            if db.query(LedgerEntry).filter(LedgerEntry.reversal_of_id == row.id).first() is not None:
                raise ValueError("Ledger de componente já possui reversão.")
            result.append(row)
        elif matches:
            raise ValueError("Ledger fictício para componente sem valor aplicado.")
    return result


def _validate_loan_settlement(payment: Payment, settlement: PaymentSettlement) -> dict[str, Decimal]:
    if settlement.receipt_version != "v4":
        raise ValueError("Reversão de Loan exige PaymentSettlement v4.")
    if settlement.payment_id != payment.id or settlement.obligation_type != "LOAN_INSTALLMENT":
        raise ValueError("Settlement incompatível com reversão de LoanInstallment.")
    if settlement.loan_installment_id is None or settlement.loan_installment_status_before is None or settlement.loan_installment_status_after is None:
        raise ValueError("Settlement v4 sem evidência completa da parcela.")
    try:
        snapshot = json.loads(settlement.receipt_snapshot_json)
    except (TypeError, ValueError) as exc:
        raise ValueError("Snapshot do settlement inválido.") from exc
    canonical = _canonical_json(snapshot)
    if canonical != settlement.receipt_snapshot_json or hashlib.sha256(canonical.encode()).hexdigest() != settlement.receipt_hash:
        raise ValueError("Receipt/hash do settlement inválido.")
    state = snapshot.get("loan_installment_state") or {}
    if state.get("status_before") != settlement.loan_installment_status_before or state.get("status_after") != settlement.loan_installment_status_after:
        raise ValueError("Evidência operacional da parcela não corresponde ao snapshot.")
    if state.get("paid_at_before") != _timestamp_iso(settlement.loan_installment_paid_at_before) or state.get("paid_at_after") != _timestamp_iso(settlement.loan_installment_paid_at_after):
        raise ValueError("paid_at operacional da parcela não corresponde ao snapshot.")
    loan_state = snapshot.get("loan_state") or {}
    expected_loan_state = {
        "status_before": settlement.loan_status_before,
        "status_after": settlement.loan_status_after,
        "state_revision_before": settlement.loan_state_revision_before,
        "state_revision_after": settlement.loan_state_revision_after,
        "paid_at_before": _timestamp_iso(settlement.loan_paid_at_before),
        "paid_at_after": _timestamp_iso(settlement.loan_paid_at_after),
    }
    if loan_state != expected_loan_state:
        raise ValueError("Evidência do Loan não corresponde ao snapshot.")
    values = _validate_money(settlement, payment, loan=True)
    expected_amounts = {
        "received": values["amount_received"], "applied": values["amount_applied"],
        "principal": values["principal_applied"], "interest": values["interest_applied"],
        "penalty": values["penalty_applied"], "excess": values["excess_amount"],
    }
    amounts = snapshot.get("amounts") or {}
    if any(amounts.get(key) != format(value, "f") for key, value in expected_amounts.items()):
        raise ValueError("Valores do settlement não correspondem ao snapshot.")
    if settlement.settlement_component_version is not None:
        component_amounts = {
            "normal_price_interest": values["normal_interest_applied"],
            "late_interest": values["late_interest_applied"],
            "fixed_penalty": values["fixed_penalty_applied"],
        }
        if any(
            amounts.get(key) != format(value, "f")
            for key, value in component_amounts.items()
        ):
            raise ValueError(
                "Componentes versionados não correspondem ao snapshot."
            )
    payment_snapshot = snapshot.get("payment") or {}
    obligation_snapshot = snapshot.get("obligation") or {}
    if payment_snapshot.get("id") != payment.id or payment_snapshot.get("reference_type") != payment.reference_type or payment_snapshot.get("reference_id") != payment.reference_id:
        raise ValueError("Identidade do Payment não corresponde ao snapshot.")
    if obligation_snapshot.get("type") != settlement.obligation_type or obligation_snapshot.get("loan_installment_id") != settlement.loan_installment_id or obligation_snapshot.get("member_id") != settlement.member_id:
        raise ValueError("Referência da obrigação não corresponde ao snapshot.")
    if any(getattr(settlement, name) is None for name in ("loan_status_before", "loan_status_after", "loan_state_revision_before", "loan_state_revision_after")):
        raise ValueError("Settlement v4 sem evidência completa do Loan.")
    if settlement.loan_state_revision_after != settlement.loan_state_revision_before + 1:
        raise ValueError("Settlement v4 possui revisão do Loan inválida.")
    return values


def _lock_agreement_installment(db: Session, installment_id: int) -> AgreementInstallment | None:
    with db.no_autoflush:
        query = db.query(AgreementInstallment).filter(AgreementInstallment.id == installment_id)
        if _is_postgresql(db):
            query = query.with_for_update().populate_existing()
        return query.one_or_none()


def _validate_agreement_settlement(
    db: Session,
    payment: Payment,
    settlement: PaymentSettlement,
) -> tuple[dict[str, Decimal], CollectionAgreement, AgreementInstallment]:
    if settlement.receipt_version != "v5":
        raise ValueError("Reversão de Agreement exige PaymentSettlement v5.")
    if settlement.payment_id != payment.id or settlement.obligation_type != "AGREEMENT_INSTALLMENT":
        raise ValueError("Settlement incompatível com reversão de AgreementInstallment.")
    if settlement.agreement_installment_id is None:
        raise ValueError("Settlement v5 sem AgreementInstallment.")
    payment_reference = (payment.reference_type or "").strip().upper()
    if payment_reference != "AGREEMENT_INSTALLMENT" or payment.reference_id != str(settlement.agreement_installment_id):
        raise ValueError("Payment não corresponde ao AgreementInstallment.")

    # The first read only discovers the parent. Actual locks follow the global
    # order: Payment -> Settlement -> CollectionAgreement -> Installment.
    with db.no_autoflush:
        locator = db.get(AgreementInstallment, settlement.agreement_installment_id)
    if locator is None:
        raise ValueError("AgreementInstallment não encontrado.")
    agreement = lock_collection_agreement(db, locator.agreement_id)
    installment = _lock_agreement_installment(db, locator.id)
    if installment is None:
        raise ValueError("AgreementInstallment não encontrado.")
    if agreement.member_id != settlement.member_id:
        raise ValueError("Settlement não corresponde ao membro do Agreement.")
    if installment.agreement_id != agreement.id:
        raise ValueError("AgreementInstallment não corresponde ao Agreement.")

    try:
        snapshot = json.loads(settlement.receipt_snapshot_json)
    except (TypeError, ValueError) as exc:
        raise ValueError("Snapshot do settlement Agreement inválido.") from exc
    canonical = _canonical_json(snapshot)
    if canonical != settlement.receipt_snapshot_json or hashlib.sha256(canonical.encode("utf-8")).hexdigest() != settlement.receipt_hash:
        raise ValueError("Receipt/hash do settlement Agreement inválido.")

    values = _validate_money(settlement, payment, loan=False)
    if values["interest_applied"] != ZERO:
        raise ValueError("Settlement Agreement não pode possuir juros.")
    if values["amount_applied"] != values["principal_applied"] + values["penalty_applied"]:
        raise ValueError("Settlement Agreement possui componentes inválidos.")
    if values["amount_received"] != values["amount_applied"] + values["excess_amount"]:
        raise ValueError("Settlement Agreement possui excesso inválido.")

    required = (
        settlement.agreement_installment_status_before,
        settlement.agreement_installment_status_after,
        settlement.agreement_installment_paid_amount_before,
        settlement.agreement_installment_paid_amount_after,
        settlement.agreement_installment_paid_penalty_amount_before,
        settlement.agreement_installment_paid_penalty_amount_after,
        settlement.collection_agreement_status_before,
        settlement.collection_agreement_status_after,
        settlement.collection_agreement_state_revision_before,
        settlement.collection_agreement_state_revision_after,
    )
    if any(value is None for value in required):
        raise ValueError("Settlement v5 sem evidência completa do Agreement.")
    if settlement.collection_agreement_status_before not in {"APPROVED", "SETTLED"} or settlement.collection_agreement_status_after not in {"APPROVED", "SETTLED"}:
        raise ValueError("Settlement v5 possui status de Agreement inválido.")
    if settlement.agreement_installment_status_before not in {"OPEN", "PARTIAL", "PAID"} or settlement.agreement_installment_status_after not in {"OPEN", "PARTIAL", "PAID"}:
        raise ValueError("Settlement v5 possui status de parcela inválido.")
    if settlement.collection_agreement_state_revision_before < 0 or settlement.collection_agreement_state_revision_after != settlement.collection_agreement_state_revision_before + 1:
        raise ValueError("Settlement v5 possui revisão de Agreement inválida.")
    if settlement.agreement_installment_paid_amount_before < ZERO or settlement.agreement_installment_paid_amount_after < ZERO or settlement.agreement_installment_paid_penalty_amount_before < ZERO or settlement.agreement_installment_paid_penalty_amount_after < ZERO:
        raise ValueError("Settlement v5 possui valores absolutos negativos.")
    if settlement.agreement_installment_paid_amount_after != settlement.agreement_installment_paid_amount_before + values["principal_applied"]:
        raise ValueError("Settlement v5 possui equação de paid_amount inválida.")
    if settlement.agreement_installment_paid_penalty_amount_after != settlement.agreement_installment_paid_penalty_amount_before + values["penalty_applied"]:
        raise ValueError("Settlement v5 possui equação de paid_penalty_amount inválida.")

    expected_snapshot = _settlement_receipt_snapshot(payment=payment, settlement=settlement, ledger=_ledger_snapshot(db, payment.id))
    if snapshot != expected_snapshot or settlement.receipt_snapshot_json != _canonical_json(expected_snapshot):
        raise ValueError("Snapshot v5 do settlement diverge das colunas.")
    obligation = snapshot.get("obligation") or {}
    if obligation.get("type") != "AGREEMENT_INSTALLMENT" or obligation.get("agreement_installment_id") != installment.id or obligation.get("member_id") != settlement.member_id:
        raise ValueError("Referência do Agreement não corresponde ao snapshot.")
    payment_snapshot = snapshot.get("payment") or {}
    if payment_snapshot.get("id") != payment.id or payment_snapshot.get("reference_type") != payment.reference_type or payment_snapshot.get("reference_id") != payment.reference_id:
        raise ValueError("Payment não corresponde ao snapshot do settlement.")

    installment_state = snapshot.get("agreement_installment_state") or {}
    expected_installment_state = {
        "status_before": settlement.agreement_installment_status_before,
        "status_after": settlement.agreement_installment_status_after,
        "paid_at_before": _timestamp_iso(settlement.agreement_installment_paid_at_before),
        "paid_at_after": _timestamp_iso(settlement.agreement_installment_paid_at_after),
        "paid_amount_before": format(_money(settlement.agreement_installment_paid_amount_before), "f"),
        "paid_amount_after": format(_money(settlement.agreement_installment_paid_amount_after), "f"),
        "paid_penalty_amount_before": format(_money(settlement.agreement_installment_paid_penalty_amount_before), "f"),
        "paid_penalty_amount_after": format(_money(settlement.agreement_installment_paid_penalty_amount_after), "f"),
    }
    agreement_state = snapshot.get("collection_agreement_state") or {}
    expected_agreement_state = {
        "status_before": settlement.collection_agreement_status_before,
        "status_after": settlement.collection_agreement_status_after,
        "state_revision_before": settlement.collection_agreement_state_revision_before,
        "state_revision_after": settlement.collection_agreement_state_revision_after,
    }
    if installment_state != expected_installment_state or agreement_state != expected_agreement_state:
        raise ValueError("Evidência operacional do Agreement diverge do snapshot.")
    return values, agreement, installment


def _agreement_reversal_snapshot(reversal, payment, settlement, agreement, installment, original, compensating):
    return {
        "receipt_version": reversal.receipt_version,
        "receipt_number": reversal.receipt_number,
        "reversal": {
            "id": reversal.id,
            "reversed_at": _timestamp_iso(reversal.reversed_at),
            "reversal_competence": reversal.reversal_competence.isoformat(),
            "admin_id": reversal.admin_id,
            "reason": reversal.reason,
        },
        "payment": {"id": payment.id, "status": payment.status, "reference_type": payment.reference_type, "reference_id": payment.reference_id},
        "settlement": {"id": settlement.id, "receipt_number": settlement.receipt_number, "receipt_version": settlement.receipt_version, "receipt_hash": settlement.receipt_hash},
        "obligation": {
            "type": "AGREEMENT_INSTALLMENT",
            "member_id": settlement.member_id,
            "agreement_installment_id": installment.id,
            "collection_agreement_id": agreement.id,
        },
        "agreement": {"id": agreement.id, "status_before": settlement.collection_agreement_status_before, "status_after": settlement.collection_agreement_status_after, "status_restored": agreement.status, "state_revision_before": settlement.collection_agreement_state_revision_before, "state_revision_after": settlement.collection_agreement_state_revision_after, "state_revision": agreement.state_revision},
        "agreement_installment": {"id": installment.id, "due_date": installment.due_date.isoformat(), "status_before": settlement.agreement_installment_status_before, "status_after": settlement.agreement_installment_status_after, "status_restored": installment.status, "paid_at_before": _timestamp_iso(settlement.agreement_installment_paid_at_before), "paid_at_after": _timestamp_iso(settlement.agreement_installment_paid_at_after), "paid_at_restored": _timestamp_iso(installment.paid_at), "paid_amount_before": format(_money(settlement.agreement_installment_paid_amount_before), "f"), "paid_amount_after": format(_money(settlement.agreement_installment_paid_amount_after), "f"), "paid_amount_restored": format(_money(installment.paid_amount), "f"), "paid_penalty_amount_before": format(_money(settlement.agreement_installment_paid_penalty_amount_before), "f"), "paid_penalty_amount_after": format(_money(settlement.agreement_installment_paid_penalty_amount_after), "f"), "paid_penalty_amount_restored": format(_money(installment.paid_penalty_amount), "f")},
        "amounts": {name: format(_money(getattr(reversal, name)), "f") for name in ("amount_received", "amount_applied", "principal_applied", "interest_applied", "penalty_applied", "excess_amount")},
        "ledger_component": {"original_ledger_entry_id": original.id, "compensating_ledger_entry_id": compensating.id, "account": original.account, "amount": format(_money(original.amount), "f"), "original_direction": original.direction, "compensating_direction": compensating.direction, "reversal_of_id": compensating.reversal_of_id},
    }


def _reverse_agreement_payment(db: Session, payment, settlement, admin_id, reason, reversed_at):
    values, agreement, installment = _validate_agreement_settlement(db, payment, settlement)
    if not _same_timestamp(installment.paid_at, settlement.agreement_installment_paid_at_after):
        raise ValueError("paid_at atual da parcela diverge do settlement v5.")
    if installment.status != settlement.agreement_installment_status_after or _money(installment.paid_amount) != _money(settlement.agreement_installment_paid_amount_after) or _money(installment.paid_penalty_amount) != _money(settlement.agreement_installment_paid_penalty_amount_after):
        raise ValueError("Estado atual da parcela diverge do settlement v5.")
    if agreement.status != settlement.collection_agreement_status_after or agreement.state_revision != settlement.collection_agreement_state_revision_after:
        raise ValueError("Estado atual do Agreement diverge do settlement v5.")
    if _money(installment.paid_amount) - values["principal_applied"] != _money(settlement.agreement_installment_paid_amount_before) or _money(installment.paid_penalty_amount) - values["penalty_applied"] != _money(settlement.agreement_installment_paid_penalty_amount_before):
        raise ValueError("Valores da parcela não permitem restauração exata.")

    with db.no_autoflush:
        query = db.query(LedgerEntry).filter(LedgerEntry.reference_id == str(payment.id)).order_by(LedgerEntry.id)
        if _is_postgresql(db):
            query = query.with_for_update()
        rows = query.all()
    originals = [row for row in rows if row.reference_type == "AGREEMENT_INSTALLMENT_PAYMENT" and row.reversal_of_id is None]
    if len(originals) != 1:
        raise ValueError("Ledger Agreement ausente, duplicado ou com componente extra.")
    original = originals[0]
    extras = [row for row in rows if row.id != original.id]
    if any(extra.reversal_of_id == original.id for extra in extras):
        raise ValueError("Ledger Agreement já possui reversão.")
    if extras:
        raise ValueError("Ledger Agreement possui componentes inesperados.")
    if original.account != "CAIXINHA" or original.direction != "CREDIT" or original.reversal_of_id is not None or _money(original.amount) != values["amount_applied"]:
        raise ValueError("Ledger Agreement incompatível.")
    if db.query(LedgerEntry).filter(LedgerEntry.reversal_of_id == original.id).first() is not None:
        raise ValueError("Ledger Agreement já possui reversão.")

    reversal = PaymentReversal(
        payment_id=payment.id, settlement_id=settlement.id, admin_id=admin_id, reason=reason,
        reversed_at=reversed_at, reversal_competence=date(reversed_at.year, reversed_at.month, 1),
        original_competence=None, original_due_date=installment.due_date,
        original_date_kind="AGREEMENT_INSTALLMENT_DUE_DATE", amount_received=values["amount_received"],
        amount_applied=values["amount_applied"], principal_applied=values["principal_applied"],
        interest_applied=values["interest_applied"], penalty_applied=values["penalty_applied"],
        excess_amount=values["excess_amount"], receipt_number=f"PIX-REV-V1-{payment.id}",
        receipt_version=REVERSAL_RECEIPT_VERSION, receipt_snapshot_json=f"pending:{payment.id}",
        receipt_hash=hashlib.sha256(f"pending:{payment.id}".encode("utf-8")).hexdigest(),
    )
    db.add(reversal)
    db.flush()
    compensating = post_entry(db, original.account, "DEBIT", _money(original.amount), "REVERSAL", str(original.id), reversal_of_id=original.id)
    db.flush()
    if compensating.id == original.id or compensating.reversal_of_id != original.id:
        raise ValueError("Compensação de Ledger Agreement incompatível.")
    component = PaymentReversalComponent(payment_reversal_id=reversal.id, original_ledger_entry_id=original.id, compensating_ledger_entry_id=compensating.id)
    db.add(component)
    db.flush()

    installment.paid_amount = _money(settlement.agreement_installment_paid_amount_before)
    installment.paid_penalty_amount = _money(settlement.agreement_installment_paid_penalty_amount_before)
    installment.status = settlement.agreement_installment_status_before
    installment.paid_at = settlement.agreement_installment_paid_at_before
    agreement.status = settlement.collection_agreement_status_before
    touch_collection_agreement(agreement)
    if agreement.state_revision != settlement.collection_agreement_state_revision_after + 1:
        raise ValueError("Revision do Agreement não avançou monotonicamente.")
    db.flush()
    snapshot = _agreement_reversal_snapshot(reversal, payment, settlement, agreement, installment, original, compensating)
    reversal.receipt_snapshot_json = _canonical_json(snapshot)
    reversal.receipt_hash = hashlib.sha256(reversal.receipt_snapshot_json.encode("utf-8")).hexdigest()
    db.add(AuditLog(actor_user_id=admin_id, action="PAYMENT_REVERSED_AGREEMENT_INSTALLMENT", entity_type="PAYMENT", entity_id=str(payment.id), details=_canonical_json(snapshot)))
    db.flush()
    return reversal


def _loan_reversal_snapshot(reversal, payment, settlement, loan, installment, original_mfe, compensating_mfe, components):
    def iso(value):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return {
        "receipt_version": reversal.receipt_version,
        "receipt_number": reversal.receipt_number,
        "reversal": {"id": reversal.id, "reversed_at": iso(reversal.reversed_at), "reversal_competence": reversal.reversal_competence.isoformat(), "admin_id": reversal.admin_id, "reason": reversal.reason},
        "payment": {"id": payment.id, "status": payment.status, "reference_type": payment.reference_type, "reference_id": payment.reference_id},
        "settlement": {"id": settlement.id, "receipt_number": settlement.receipt_number, "receipt_version": settlement.receipt_version, "receipt_hash": settlement.receipt_hash},
        "member": {"id": settlement.member_id},
        "loan": {"id": loan.id, "status_before": settlement.loan_status_before, "status_after": settlement.loan_status_after, "state_revision_before": settlement.loan_state_revision_before, "state_revision_after": settlement.loan_state_revision_after, "status_restored": loan.status, "state_revision": loan.state_revision, "paid_at_before": iso(settlement.loan_paid_at_before), "paid_at_after": iso(settlement.loan_paid_at_after)},
        "loan_installment": {"id": installment.id, "due_date": installment.due_date.isoformat(), "status_before": settlement.loan_installment_status_before, "status_after": settlement.loan_installment_status_after, "paid_at_before": iso(settlement.loan_installment_paid_at_before), "paid_at_after": iso(settlement.loan_installment_paid_at_after), "status_restored": installment.status, "paid_amount_restored": format(_money(installment.paid_amount), "f"), "paid_penalty_amount_restored": format(_money(installment.paid_penalty_amount), "f")},
        "amounts": {name: format(_money(getattr(reversal, name)), "f") for name in ("amount_received", "amount_applied", "principal_applied", "interest_applied", "penalty_applied", "excess_amount")},
        "member_financial": {"original_entry_id": original_mfe.id if original_mfe else None, "compensating_entry_id": compensating_mfe.id if compensating_mfe else None},
        "ledger_components": [{"original_ledger_entry_id": c.original_ledger_entry_id, "compensating_ledger_entry_id": c.compensating_ledger_entry_id, "reversal_of_id": c.compensating_ledger_entry.reversal_of_id, "amount": format(_money(c.original_ledger_entry.amount), "f"), "original_direction": c.original_ledger_entry.direction, "compensating_direction": c.compensating_ledger_entry.direction} for c in sorted(components, key=lambda item: item.original_ledger_entry_id)],
    }


def _reverse_loan_payment(db, payment, settlement, admin_id, reason, reversed_at):
    values = _validate_loan_settlement(payment, settlement)
    member, account = lock_member_financial_account(db, settlement.member_id)
    unlocked_installment = db.get(LoanInstallment, settlement.loan_installment_id)
    if unlocked_installment is None or payment.reference_type != "LOAN_INSTALLMENT" or payment.reference_id != str(unlocked_installment.id):
        raise ValueError("Payment não corresponde à LoanInstallment.")
    loan = lock_loan(db, db.get(Loan, unlocked_installment.loan_id))
    installment = _lock_installment(db, unlocked_installment.id)
    if installment is None:
        raise ValueError("LoanInstallment não encontrado.")
    if loan.member_id != member.id or loan.status != settlement.loan_status_after or loan.state_revision != settlement.loan_state_revision_after:
        raise ValueError("Estado atual do Loan diverge do settlement v4.")
    if not _same_timestamp(loan.paid_at, settlement.loan_paid_at_after):
        raise ValueError("paid_at atual do Loan diverge do settlement v4.")
    if installment.status != settlement.loan_installment_status_after or not _same_timestamp(installment.paid_at, settlement.loan_installment_paid_at_after):
        raise ValueError("Estado atual da parcela diverge do settlement v4.")
    with db.no_autoflush:
        query = db.query(MemberFinancialEntry).filter(MemberFinancialEntry.account_id == account.id, MemberFinancialEntry.reference_id == str(payment.id))
        if _is_postgresql(db):
            query = query.with_for_update()
        entries = query.all()
    principal_candidates = [e for e in entries if e.entry_type == "LOAN_PRINCIPAL_PAYMENT"]
    original_mfe = None
    if values["principal_applied"] > ZERO:
        candidates = [e for e in entries if e.entry_type == "LOAN_PRINCIPAL_PAYMENT"]
        matches = [e for e in candidates if e.direction == "CREDIT" and e.reference_type == "LOAN_PRINCIPAL_PAYMENT" and _money(e.amount) == values["principal_applied"]]
        if len(candidates) != 1 or len(matches) != 1:
            raise ValueError("MFE original de principal ausente, duplicada ou incompatível.")
        original_mfe = matches[0]
        if get_member_financial_position(db, member)["own_balance"] < values["principal_applied"]:
            raise ValueError("Saldo próprio insuficiente para estorno integral.")
    elif principal_candidates:
        raise ValueError("MFE de principal inesperada para settlement sem principal.")
    originals = _loan_ledgers(db, payment.id, values)
    base = values["principal_applied"] + values["interest_applied"]
    if _money(installment.paid_amount) < base or _money(installment.paid_penalty_amount) < values["penalty_applied"]:
        raise ValueError("Parcela não possui valores pagos suficientes para restauração.")
    if settlement.settlement_component_version is not None:
        if (
            installment.paid_fixed_penalty_amount is None
            and values["fixed_penalty_applied"] != ZERO
        ) or (
            installment.paid_late_interest_amount is None
            and values["late_interest_applied"] != ZERO
        ):
            raise ValueError(
                "Projeções versionadas da parcela estão ausentes."
            )
        if (
            _money(installment.paid_fixed_penalty_amount)
            < values["fixed_penalty_applied"]
            or _money(installment.paid_late_interest_amount)
            < values["late_interest_applied"]
        ):
            raise ValueError(
                "Projeções versionadas não permitem restauração exata."
            )
    reversal = PaymentReversal(payment_id=payment.id, settlement_id=settlement.id, admin_id=admin_id, reason=reason, reversed_at=reversed_at, reversal_competence=date(reversed_at.year, reversed_at.month, 1), original_competence=None, original_due_date=installment.due_date, original_date_kind="LOAN_INSTALLMENT_DUE_DATE", amount_received=values["amount_received"], amount_applied=values["amount_applied"], principal_applied=values["principal_applied"], interest_applied=values["interest_applied"], penalty_applied=values["penalty_applied"], excess_amount=values["excess_amount"], receipt_number=f"PIX-REV-V1-{payment.id}", receipt_version=REVERSAL_RECEIPT_VERSION, receipt_snapshot_json=f"pending:{payment.id}", receipt_hash=hashlib.sha256(f"pending:{payment.id}".encode()).hexdigest())
    db.add(reversal)
    db.flush()
    compensating_mfe = None
    if original_mfe is not None:
        compensating_mfe = add_member_financial_entry(db, member, entry_type="LOAN_PRINCIPAL_REVERSAL", direction="DEBIT", amount=values["principal_applied"], reference_type="PAYMENT_REVERSAL", reference_id=str(reversal.id), description="Compensação de principal de pagamento estornado.", account=account)
        compensating_mfe.payment_reversal_id = reversal.id
        db.flush()
    components = []
    for original in originals:
        compensating = post_entry(db, original.account, "DEBIT" if original.direction == "CREDIT" else "CREDIT", _money(original.amount), "REVERSAL", str(original.id), reversal_of_id=original.id)
        db.flush()
        component = PaymentReversalComponent(payment_reversal_id=reversal.id, original_ledger_entry_id=original.id, compensating_ledger_entry_id=compensating.id)
        db.add(component)
        components.append(component)
    db.flush()
    installment.paid_amount = _money(installment.paid_amount) - base
    installment.paid_penalty_amount = _money(installment.paid_penalty_amount) - values["penalty_applied"]
    if settlement.settlement_component_version is not None:
        if installment.paid_fixed_penalty_amount is not None:
            installment.paid_fixed_penalty_amount = (
                _money(installment.paid_fixed_penalty_amount)
                - values["fixed_penalty_applied"]
            )
        if installment.paid_late_interest_amount is not None:
            installment.paid_late_interest_amount = (
                _money(installment.paid_late_interest_amount)
                - values["late_interest_applied"]
            )
    installment.status = settlement.loan_installment_status_before
    installment.paid_at = settlement.loan_installment_paid_at_before
    loan.status = settlement.loan_status_before
    loan.paid_at = settlement.loan_paid_at_before
    touch_loan(loan)
    db.flush()
    if settlement.settlement_component_version is not None:
        materialize_reversal_late_interest_adjustment(db, reversal.id)
        if installment.late_charge_version == LATE_CHARGE_VERSION:
            installment.penalty_amount = _money(
                installment.fixed_penalty_amount
            ) + _money(installment.late_interest_amount)
    snapshot = _loan_reversal_snapshot(reversal, payment, settlement, loan, installment, original_mfe, compensating_mfe, components)
    reversal.receipt_snapshot_json = _canonical_json(snapshot)
    reversal.receipt_hash = hashlib.sha256(reversal.receipt_snapshot_json.encode()).hexdigest()
    db.add(AuditLog(actor_user_id=admin_id, action="PAYMENT_REVERSED_LOAN_INSTALLMENT", entity_type="PAYMENT", entity_id=str(payment.id), details=_canonical_json(snapshot)))
    db.flush()
    return reversal


def reverse_payment(
    db: Session,
    payment_id: int | Payment = None,
    admin_id: int | User = None,
    reason: str = "",
    now: datetime | None = None,
    *,
    payment: Payment | None = None,
    admin: User | None = None,
) -> PaymentReversal:
    """Reverse a Contribution or v4 LoanInstallment payment, without commit."""
    if payment is not None:
        payment_id = payment.id
    elif isinstance(payment_id, Payment):
        payment = payment_id
        payment_id = payment.id
    if admin is not None:
        admin_id = admin.id
    if isinstance(admin_id, User):
        admin_id = admin_id.id
    if payment_id is None or admin_id is None:
        raise ValueError("Pagamento e administrador são obrigatórios.")
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
    if settlement.obligation_type == "LOAN_INSTALLMENT":
        return _reverse_loan_payment(db, payment, settlement, admin_id, normalized_reason, reversed_at)
    if settlement.obligation_type == "AGREEMENT_INSTALLMENT":
        return _reverse_agreement_payment(db, payment, settlement, admin_id, normalized_reason, reversed_at)
    if settlement.contribution_id is None:
        raise ValueError("Settlement não referencia Contribution.")
    # Discover the Member without taking the Contribution lock first. The
    # final financial decision is made only after Member -> Account is held.
    contribution_locator = db.get(Contribution, settlement.contribution_id)
    if contribution_locator is None:
        raise ValueError("Contribution não encontrada.")
    member, account = lock_member_financial_account(db, contribution_locator.member_id)
    contribution = _lock_contribution(db, settlement.contribution_id)
    if contribution is None:
        raise ValueError("Contribution não encontrada.")
    if contribution.member_id != member.id:
        raise ValueError("Contribution não corresponde ao Member do settlement.")
    _validate_contribution_reference(payment, settlement, contribution)
    values = _validate_money(settlement, payment)
    if values["amount_applied"] > _money(contribution.amount):
        raise ValueError("Settlement excede o valor da Contribution.")

    current_paid = _money(contribution.paid_amount)
    if current_paid < values["amount_applied"]:
        raise ValueError("Contribution não possui valor pago suficiente para o estorno.")
    original_status = contribution.status
    before_financial_status = contribution_financial_status(contribution, current_paid, reversed_at)
    new_paid = current_paid - values["amount_applied"]
    after_status = contribution_financial_status(contribution, new_paid, reversed_at)
    patrimonial_reversal_amount = ZERO
    if before_financial_status == "PAID" and after_status != "PAID":
        patrimonial_reversal_amount = _active_contribution_credit(db, account.id, contribution.id)
        if patrimonial_reversal_amount not in {ZERO, _money(contribution.amount)}:
            raise ValueError("Crédito patrimonial da Contribution é inconsistente.")
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
    if patrimonial_reversal_amount > ZERO:
        compensating_mfe = add_member_financial_entry(
            db,
            member,
            entry_type="CONTRIBUTION_REVERSAL",
            direction="DEBIT",
            amount=patrimonial_reversal_amount,
            reference_type="PAYMENT_REVERSAL",
            reference_id=str(reversal.id),
            description="Reversão do crédito patrimonial de Contribution.",
            account=account,
        )
        compensating_mfe.contribution_id = contribution.id
        compensating_mfe.payment_reversal_id = reversal.id
        db.flush()

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
