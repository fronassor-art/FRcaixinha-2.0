"""Transactional, idempotent settlement of provider-confirmed PIX payments."""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import AgreementInstallment, CollectionAgreement, Contribution, LedgerEntry, Loan, LoanInstallment, Member, Payment, PaymentSettlement
from app.services.ledger import post_contribution_payment, post_entry
from app.services.loan_engine_v17 import lock_loan
from app.services.loan_payments_v17 import apply_confirmed_payment
from app.services.member_financial import add_member_financial_entry, lock_member_financial_account
from app.services.agreements_v039 import lock_collection_agreement, touch_collection_agreement


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


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        # SQLite's DateTime implementation may return a naive value after
        # reload; the project convention treats that stored value as UTC.
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


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


def _locked_contribution(
    db: Session,
    payment: Payment,
    *,
    lock: bool = True,
    refresh: bool = False,
) -> Contribution | None:
    contribution_id = None
    if (payment.reference_type or "").upper() == "CONTRIBUTION" and (payment.reference_id or "").isdigit():
        contribution_id = int(payment.reference_id)
    query = db.query(Contribution)
    if contribution_id is not None:
        query = query.filter(Contribution.id == contribution_id)
    else:
        query = query.filter(Contribution.payment_id == payment.id)
    if lock and _is_postgresql(db):
        query = query.with_for_update()
    if refresh and _is_postgresql(db):
        query = query.populate_existing()
    contribution = query.one_or_none()
    if contribution is not None and payment.reference_type and (payment.reference_type or "").upper() == "CONTRIBUTION":
        if contribution_id is None or contribution.id != contribution_id:
            raise ValueError("Referência de contribuição inválida.")
    return contribution


def _locked_installment(db: Session, payment: Payment, *, lock: bool = True, refresh: bool = False) -> LoanInstallment | None:
    if (payment.reference_type or "").upper() != "LOAN_INSTALLMENT" or not (payment.reference_id or "").isdigit():
        return None
    with db.no_autoflush:
        query = db.query(LoanInstallment).filter(LoanInstallment.id == int(payment.reference_id))
        if lock and _is_postgresql(db):
            query = query.with_for_update()
        if refresh and _is_postgresql(db):
            query = query.populate_existing()
        return query.one_or_none()


def _locked_agreement_installment(db: Session, payment: Payment) -> AgreementInstallment | None:
    if (payment.reference_type or "").upper() != "AGREEMENT_INSTALLMENT" or not (payment.reference_id or "").isdigit():
        return None
    with db.no_autoflush:
        query = db.query(AgreementInstallment).filter(AgreementInstallment.id == int(payment.reference_id))
        if _is_postgresql(db):
            query = query.with_for_update().populate_existing()
        return query.one_or_none()


def _locked_collection_agreement(db: Session, agreement_id: int) -> CollectionAgreement | None:
    try:
        return lock_collection_agreement(db, agreement_id)
    except ValueError:
        return None


def _lock_payment_settlement(db: Session, payment_id: int) -> PaymentSettlement | None:
    with db.no_autoflush:
        query = db.query(PaymentSettlement).filter(PaymentSettlement.payment_id == payment_id)
        if _is_postgresql(db):
            query = query.with_for_update().populate_existing()
        return query.one_or_none()


def _legacy_agreement_ledger_exists(db: Session, payment_id: int) -> bool:
    return db.query(LedgerEntry).filter(
        LedgerEntry.reference_type == "AGREEMENT_INSTALLMENT_PAYMENT",
        LedgerEntry.reference_id == str(payment_id),
    ).first() is not None


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
    obligation = {
        "type": settlement.obligation_type,
        "contribution_id": settlement.contribution_id,
        "loan_installment_id": settlement.loan_installment_id,
        "member_id": settlement.member_id,
        "status_before": settlement.obligation_status_before,
        "status_after": settlement.obligation_status_after,
    }
    if settlement.obligation_type == "AGREEMENT_INSTALLMENT":
        obligation["agreement_installment_id"] = settlement.agreement_installment_id
    snapshot = {
        "receipt_version": settlement.receipt_version,
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
        "obligation": obligation,
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
    if settlement.receipt_version == "v2":
        snapshot["loan_state"] = {
            "status_before": settlement.loan_status_before,
            "status_after": settlement.loan_status_after,
            "state_revision_before": settlement.loan_state_revision_before,
            "state_revision_after": settlement.loan_state_revision_after,
        }
    elif settlement.receipt_version == "v3":
        snapshot["loan_state"] = {
            "status_before": settlement.loan_status_before,
            "status_after": settlement.loan_status_after,
            "state_revision_before": settlement.loan_state_revision_before,
            "state_revision_after": settlement.loan_state_revision_after,
            "paid_at_before": (
                settlement.loan_paid_at_before.astimezone(timezone.utc).isoformat()
                if settlement.loan_paid_at_before is not None else None
            ),
            "paid_at_after": (
                settlement.loan_paid_at_after.astimezone(timezone.utc).isoformat()
                if settlement.loan_paid_at_after is not None else None
            ),
        }
    elif settlement.receipt_version == "v4":
        snapshot["loan_state"] = {
            "status_before": settlement.loan_status_before,
            "status_after": settlement.loan_status_after,
            "state_revision_before": settlement.loan_state_revision_before,
            "state_revision_after": settlement.loan_state_revision_after,
            "paid_at_before": _utc_iso(settlement.loan_paid_at_before),
            "paid_at_after": _utc_iso(settlement.loan_paid_at_after),
        }
        snapshot["loan_installment_state"] = {
            "status_before": settlement.loan_installment_status_before,
            "status_after": settlement.loan_installment_status_after,
            "paid_at_before": _utc_iso(settlement.loan_installment_paid_at_before),
            "paid_at_after": _utc_iso(settlement.loan_installment_paid_at_after),
        }
    elif settlement.receipt_version == "v5":
        snapshot["agreement_installment_state"] = {
            "status_before": settlement.agreement_installment_status_before,
            "status_after": settlement.agreement_installment_status_after,
            "paid_at_before": _utc_iso(settlement.agreement_installment_paid_at_before),
            "paid_at_after": _utc_iso(settlement.agreement_installment_paid_at_after),
            "paid_amount_before": format(_money(settlement.agreement_installment_paid_amount_before), "f"),
            "paid_amount_after": format(_money(settlement.agreement_installment_paid_amount_after), "f"),
            "paid_penalty_amount_before": format(_money(settlement.agreement_installment_paid_penalty_amount_before), "f"),
            "paid_penalty_amount_after": format(_money(settlement.agreement_installment_paid_penalty_amount_after), "f"),
        }
        snapshot["collection_agreement_state"] = {
            "status_before": settlement.collection_agreement_status_before,
            "status_after": settlement.collection_agreement_status_after,
            "state_revision_before": settlement.collection_agreement_state_revision_before,
            "state_revision_after": settlement.collection_agreement_state_revision_after,
        }
    return snapshot


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
    existing = _lock_payment_settlement(db, payment.id)
    if existing is not None:
        return existing
    reference_type = (payment.reference_type or "").strip().upper()
    if reference_type == "AGREEMENT_INSTALLMENT" and (payment.ledger_posted_at is not None or _legacy_agreement_ledger_exists(db, payment.id)):
        raise ValueError("Pagamento legado de acordo já lançado não pode receber settlement retroativo.")
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

    # Read only to discover the Member. The financial decision and final
    # Contribution state are reloaded after acquiring Member -> Account.
    contribution = _locked_contribution(db, payment, lock=False)
    installment = _locked_installment(db, payment, lock=False)
    agreement_installment = None
    agreement = None
    if reference_type == "AGREEMENT_INSTALLMENT":
        if payment.status != "approved":
            raise ValueError("Pagamento de acordo precisa estar aprovado.")
        if not (payment.reference_id or "").isdigit():
            raise ValueError("Referência de parcela de acordo inválida.")
        # Read only to discover the parent; actual locks follow Agreement -> Installment.
        with db.no_autoflush:
            locator = db.query(AgreementInstallment).filter(AgreementInstallment.id == int(payment.reference_id)).one_or_none()
        if locator is not None:
            agreement = lock_collection_agreement(db, locator.agreement_id)
        agreement_installment = _locked_agreement_installment(db, payment)
        if agreement_installment is None:
            raise ValueError("Referência de parcela de acordo inválida.")
        if agreement is None:
            raise ValueError("Acordo da parcela não encontrado.")
    elif (contribution is None) == (installment is None):
        raise ValueError("Pagamento deve referenciar exatamente uma contribuição ou parcela de empréstimo.")

    penalty_applied = interest_applied = principal_applied = ZERO
    loan_status_before = loan_status_after = None
    loan_state_revision_before = loan_state_revision_after = None
    loan_paid_at_before = loan_paid_at_after = None
    loan_installment_status_before = loan_installment_status_after = None
    loan_installment_paid_at_before = loan_installment_paid_at_after = None
    agreement_installment_status_before = agreement_installment_status_after = None
    agreement_installment_paid_at_before = agreement_installment_paid_at_after = None
    agreement_installment_paid_amount_before = agreement_installment_paid_amount_after = None
    agreement_installment_paid_penalty_amount_before = agreement_installment_paid_penalty_amount_after = None
    collection_agreement_status_before = collection_agreement_status_after = None
    collection_agreement_state_revision_before = collection_agreement_state_revision_after = None
    contribution_became_paid = False
    if agreement_installment is not None:
        member = db.get(Member, agreement.member_id)
        if member is None:
            raise ValueError("Participante do acordo não encontrado.")
        agreement_installment_status_before = agreement_installment.status
        agreement_installment_paid_at_before = agreement_installment.paid_at
        agreement_installment_paid_amount_before = _money(agreement_installment.paid_amount)
        agreement_installment_paid_penalty_amount_before = _money(agreement_installment.paid_penalty_amount)
        collection_agreement_status_before = agreement.status
        collection_agreement_state_revision_before = int(agreement.state_revision or 0)
        before_status = agreement_installment.status
        before_penalty = _money(agreement_installment.paid_penalty_amount)
        before_principal = _money(agreement_installment.paid_amount)
        penalty_open = max(ZERO, _money(agreement_installment.penalty_amount) - before_penalty)
        principal_open = max(ZERO, _money(agreement_installment.principal) - before_principal)
        due = _money(penalty_open + principal_open)
        applied = min(received, due)
        if applied <= ZERO:
            raise ValueError("Parcela de acordo não possui saldo aplicável.")
        penalty_applied = min(penalty_open, applied)
        principal_applied = min(principal_open, _money(applied - penalty_applied))
        agreement_installment.paid_penalty_amount = _money(before_penalty + penalty_applied)
        agreement_installment.paid_amount = _money(before_principal + principal_applied)
        remaining = max(ZERO, _money(agreement_installment.penalty_amount) - agreement_installment.paid_penalty_amount)
        remaining += max(ZERO, _money(agreement_installment.principal) - agreement_installment.paid_amount)
        after_status = "PAID" if remaining == ZERO else ("PARTIAL" if applied > ZERO else "OPEN")
        agreement_installment.status = after_status
        if after_status == "PAID":
            agreement_installment.paid_at = effective_at
        post_entry(db, "CAIXINHA", "CREDIT", applied, "AGREEMENT_INSTALLMENT_PAYMENT", str(payment.id))
        items = db.query(AgreementInstallment).filter(AgreementInstallment.agreement_id == agreement.id).all()
        if items and all(item.status == "PAID" for item in items):
            agreement.status = "SETTLED"
        touch_collection_agreement(agreement)
        agreement_installment_status_after = agreement_installment.status
        agreement_installment_paid_at_after = agreement_installment.paid_at
        agreement_installment_paid_amount_after = _money(agreement_installment.paid_amount)
        agreement_installment_paid_penalty_amount_after = _money(agreement_installment.paid_penalty_amount)
        collection_agreement_status_after = agreement.status
        collection_agreement_state_revision_after = agreement.state_revision
        obligation_type = "AGREEMENT_INSTALLMENT"
        member_id = member.id
        contribution_id = None
        loan_installment_id = None
        agreement_installment_id = agreement_installment.id
    elif contribution is not None:
        member, account = lock_member_financial_account(db, contribution.member_id)
        contribution = _locked_contribution(db, payment, lock=True, refresh=True)
        if contribution is None or contribution.member_id != member.id:
            raise ValueError("Referência de contribuição inválida.")
        before_paid = _contribution_paid_amount(contribution)
        before_status = contribution_financial_status(contribution, before_paid, effective_at)
        open_amount = max(ZERO, _money(contribution.amount) - before_paid)
        applied = min(received, open_amount)
        principal_applied = applied
        after_paid = _money(before_paid + applied)
        contribution.paid_amount = after_paid
        after_status = contribution_financial_status(contribution, after_paid, effective_at)
        contribution_became_paid = before_status != "PAID" and after_status == "PAID"
        contribution.status = after_status
        if after_status == "PAID" and contribution.paid_at is None:
            contribution.paid_at = effective_at
        if applied > ZERO:
            post_contribution_payment(db, payment, amount=applied)
        obligation_type = "CONTRIBUTION"
        member_id = contribution.member_id
        contribution_id = contribution.id
        loan_installment_id = None
        agreement_installment_id = None
    else:
        assert installment is not None
        unlocked_loan = db.get(Loan, installment.loan_id)
        if unlocked_loan is None:
            raise ValueError("Empréstimo da parcela não encontrado.")
        member, account = lock_member_financial_account(db, unlocked_loan.member_id)
        loan = lock_loan(db, db.get(Loan, unlocked_loan.id))
        installment = _locked_installment(db, payment, lock=True, refresh=True)
        if installment is None:
            raise ValueError("Parcela de empréstimo não encontrada.")
        if loan is None:
            raise ValueError("Empréstimo da parcela não encontrado.")
        loan_status_before = loan.status
        loan_state_revision_before = loan.state_revision
        loan_paid_at_before = loan.paid_at
        loan_installment_status_before = installment.status
        loan_installment_paid_at_before = installment.paid_at
        before_penalty = _money(installment.paid_penalty_amount)
        before_base = _money(installment.paid_amount)
        before_status = installment_financial_status(installment, effective_at)
        interest_open = max(ZERO, _money(installment.interest) - min(_money(installment.interest), before_base))
        apply_confirmed_payment(
            db,
            payment,
            installment,
            amount=received,
            locks_acquired=True,
            loan=loan,
            member=member,
            account=account,
        )
        loan_status_after = loan.status
        loan_state_revision_after = loan.state_revision
        loan_paid_at_after = loan.paid_at
        loan_installment_status_after = installment.status
        loan_installment_paid_at_after = installment.paid_at
        if loan_state_revision_after != loan_state_revision_before + 1:
            raise ValueError("Revisão do Loan inválida para settlement v4.")
        penalty_applied = _money(installment.paid_penalty_amount) - before_penalty
        base_applied = _money(installment.paid_amount) - before_base
        interest_applied = min(base_applied, interest_open)
        principal_applied = max(ZERO, base_applied - interest_applied)
        applied = _money(penalty_applied + base_applied)
        after_status = installment_financial_status(installment, effective_at)
        obligation_type = "LOAN_INSTALLMENT"
        member_id = member.id
        contribution_id = None
        loan_installment_id = installment.id
        agreement_installment_id = None

    # Persist provider confirmation only after the obligation has accepted a
    # positive application; fully-excess Agreement payments remain untouched.
    payment.amount_received = received
    if payment.confirmed_at is None:
        payment.confirmed_at = effective_at
    _persist_remote_payload(payment, remote_payload)
    excess = _money(received - applied)
    payment.ledger_posted_at = datetime.now(timezone.utc)
    settlement_receipt_version = (
        "v4" if obligation_type == "LOAN_INSTALLMENT"
        else "v5" if obligation_type == "AGREEMENT_INSTALLMENT"
        else RECEIPT_VERSION
    )
    if settlement_receipt_version == "v4":
        if loan_installment_status_before is None or loan_installment_status_after is None:
            raise ValueError("Settlement v4 exige o status operacional da parcela.")
        if any(value is None for value in (
            loan_status_before,
            loan_status_after,
            loan_state_revision_before,
            loan_state_revision_after,
        )):
            raise ValueError("Settlement v4 exige evidência completa do Loan.")
        if loan_state_revision_after != loan_state_revision_before + 1:
            raise ValueError("Settlement v4 possui revisão do Loan inválida.")
        if loan_status_before == "PAID" or loan_paid_at_before is not None:
            raise ValueError("Settlement v4 possui paid_at_before incompatível.")
        if loan_status_after == "PAID":
            if loan_paid_at_after is None:
                raise ValueError("Settlement v4 exige paid_at_after ao fechar o Loan.")
        elif loan_paid_at_after is not None:
            raise ValueError("Settlement v4 não pode possuir paid_at_after fora de PAID.")
    if settlement_receipt_version == "v5":
        required = (
            agreement_installment_status_before, agreement_installment_status_after,
            agreement_installment_paid_amount_before, agreement_installment_paid_amount_after,
            agreement_installment_paid_penalty_amount_before, agreement_installment_paid_penalty_amount_after,
            collection_agreement_status_before, collection_agreement_status_after,
            collection_agreement_state_revision_before, collection_agreement_state_revision_after,
        )
        if any(value is None for value in required):
            raise ValueError("Settlement v5 exige evidência completa do Agreement.")
        if collection_agreement_state_revision_after != collection_agreement_state_revision_before + 1:
            raise ValueError("Settlement v5 possui revisão do Agreement inválida.")
        if agreement_installment_paid_amount_after != agreement_installment_paid_amount_before + principal_applied:
            raise ValueError("Settlement v5 possui equação de paid_amount inválida.")
        if agreement_installment_paid_penalty_amount_after != agreement_installment_paid_penalty_amount_before + penalty_applied:
            raise ValueError("Settlement v5 possui equação de paid_penalty_amount inválida.")
    receipt_number = f"PIX-{settlement_receipt_version.upper()}-{payment.id:012d}"
    settlement = PaymentSettlement(
        payment_id=payment.id,
        member_id=member_id,
        obligation_type=obligation_type,
        contribution_id=contribution_id,
        loan_installment_id=loan_installment_id,
        agreement_installment_id=agreement_installment_id,
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
        receipt_version=settlement_receipt_version,
        receipt_snapshot_json="",
        receipt_hash="",
        loan_status_before=loan_status_before,
        loan_status_after=loan_status_after,
        loan_state_revision_before=loan_state_revision_before,
        loan_state_revision_after=loan_state_revision_after,
        loan_paid_at_before=loan_paid_at_before,
        loan_paid_at_after=loan_paid_at_after,
        loan_installment_status_before=loan_installment_status_before,
        loan_installment_status_after=loan_installment_status_after,
        loan_installment_paid_at_before=loan_installment_paid_at_before,
        loan_installment_paid_at_after=loan_installment_paid_at_after,
        agreement_installment_status_before=agreement_installment_status_before,
        agreement_installment_status_after=agreement_installment_status_after,
        agreement_installment_paid_at_before=agreement_installment_paid_at_before,
        agreement_installment_paid_at_after=agreement_installment_paid_at_after,
        agreement_installment_paid_amount_before=agreement_installment_paid_amount_before,
        agreement_installment_paid_amount_after=agreement_installment_paid_amount_after,
        agreement_installment_paid_penalty_amount_before=agreement_installment_paid_penalty_amount_before,
        agreement_installment_paid_penalty_amount_after=agreement_installment_paid_penalty_amount_after,
        collection_agreement_status_before=collection_agreement_status_before,
        collection_agreement_status_after=collection_agreement_status_after,
        collection_agreement_state_revision_before=collection_agreement_state_revision_before,
        collection_agreement_state_revision_after=collection_agreement_state_revision_after,
    )
    db.add(settlement)
    db.flush()
    if contribution_became_paid:
        add_member_financial_entry(
            db,
            member,
            entry_type="CONTRIBUTION",
            direction="CREDIT",
            amount=_money(contribution.amount),
            reference_type="PAYMENT_SETTLEMENT",
            reference_id=str(settlement.id),
            description="Crédito patrimonial de Contribution confirmada.",
            account=account,
            contribution_id=contribution.id,
            payment_settlement_id=settlement.id,
        )
    snapshot = _receipt_snapshot(payment=payment, settlement=settlement, ledger=_ledger_snapshot(db, payment.id))
    canonical_snapshot = _canonical_json(snapshot)
    settlement.receipt_snapshot_json = canonical_snapshot
    settlement.receipt_hash = hashlib.sha256(canonical_snapshot.encode("utf-8")).hexdigest()
    db.flush()
    return settlement
