"""Opt-in temporal receipt evidence builders and read-only verifiers.

These helpers deliberately are not called by settlement or reversal writers.
They provide the v6/v2 evidence contract for a later, separately gated
writer rollout. Legacy receipt serializers remain untouched.
"""

import copy
import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    AgreementInstallment,
    Contribution,
    LedgerEntry,
    LoanInstallment,
    MemberFinancialAccount,
    MemberFinancialEntry,
    Payment,
    PaymentReversal,
    PaymentReversalComponent,
    PaymentSettlement,
)
from app.services.late_charge_v1 import financial_civil_date
from app.services.ledger import verify_ledger_chain
from app.services.payment_settlement import _canonical_json, _ledger_snapshot, _money, _receipt_snapshot


SETTLEMENT_TEMPORAL_VERSION = "v6"
REVERSAL_TEMPORAL_VERSION = "v2"
_SETTLEMENT_LEGACY_VERSION = {
    "CONTRIBUTION": "v1",
    "LOAN_INSTALLMENT": "v4",
    "AGREEMENT_INSTALLMENT": "v5",
}


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("timestamp econômico inválido")
    if value.tzinfo is None:
        # SQLite drops timezone metadata for timezone-aware columns; the
        # established database convention stores/reloads these values as UTC.
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _financial_date(value: date) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError("financial_date deve ser DATE")
    return value


def _expected_financial_date(value: datetime) -> date:
    return financial_civil_date(_aware_utc(value))


def _ledger_evidence(row: LedgerEntry) -> dict[str, Any]:
    return {
        "id": row.id,
        "account": row.account,
        "direction": row.direction,
        "amount": format(_money(row.amount), "f"),
        "reference_type": row.reference_type,
        "reference_id": row.reference_id,
        "reversal_of_id": row.reversal_of_id,
        "previous_hash": row.previous_hash,
        "entry_hash": row.entry_hash,
        "hash_version": row.hash_version,
        "financial_date": row.financial_date.isoformat() if row.financial_date else None,
    }


def _mfe_evidence(row: MemberFinancialEntry) -> dict[str, Any]:
    return {
        "id": row.id,
        "account_id": row.account_id,
        "member_id": row.account.member_id,
        "entry_type": row.entry_type,
        "direction": row.direction,
        "amount": format(_money(row.amount), "f"),
        "reference_type": row.reference_type,
        "reference_id": row.reference_id,
        "contribution_id": row.contribution_id,
        "payment_settlement_id": row.payment_settlement_id,
        "payment_reversal_id": row.payment_reversal_id,
    }


def _settlement_mfes(db: Session, payment: Payment, settlement: PaymentSettlement) -> list[MemberFinancialEntry]:
    if settlement.obligation_type == "LOAN_INSTALLMENT":
        rows = db.query(MemberFinancialEntry).join(
            MemberFinancialAccount, MemberFinancialEntry.account_id == MemberFinancialAccount.id
        ).filter(
            MemberFinancialEntry.entry_type == "LOAN_PRINCIPAL_PAYMENT",
            MemberFinancialEntry.reference_type == "LOAN_PRINCIPAL_PAYMENT",
            MemberFinancialEntry.reference_id == str(payment.id),
            MemberFinancialAccount.member_id == settlement.member_id,
        ).order_by(MemberFinancialEntry.id).all()
        expected = _money(settlement.principal_applied)
        if expected > 0:
            if len(rows) != 1 or rows[0].direction != "CREDIT" or _money(rows[0].amount) != expected:
                raise ValueError("MFE de principal Loan ausente, duplicado ou incompatível")
        elif rows:
            raise ValueError("MFE inesperado para principal Loan zero")
        return rows

    if settlement.obligation_type == "CONTRIBUTION":
        rows = db.query(MemberFinancialEntry).filter(
            MemberFinancialEntry.payment_settlement_id == settlement.id
        ).order_by(MemberFinancialEntry.id).all()
        expected = settlement.obligation_status_after == "PAID" and settlement.obligation_status_before != "PAID"
        if expected:
            contribution = db.get(Contribution, settlement.contribution_id)
            if contribution is None or len(rows) != 1 or rows[0].entry_type != "CONTRIBUTION" or rows[0].direction != "CREDIT" or _money(rows[0].amount) != _money(contribution.amount) or rows[0].reference_type != "PAYMENT_SETTLEMENT" or rows[0].reference_id != str(settlement.id) or rows[0].contribution_id != contribution.id or rows[0].account.member_id != settlement.member_id:
                raise ValueError("MFE de Contribution ausente, duplicado ou incompatível")
        elif rows:
            raise ValueError("MFE inesperado para Contribution sem transição patrimonial")
        return rows

    if settlement.obligation_type == "AGREEMENT_INSTALLMENT":
        return []
    raise ValueError("obligation_type sem contrato temporal")


def _assert_settlement_ledger(db: Session, payment: Payment, settlement: PaymentSettlement) -> list[LedgerEntry]:
    rows = db.query(LedgerEntry).filter(LedgerEntry.reference_id == str(payment.id)).order_by(LedgerEntry.id).all()
    expected: dict[str, Decimal] = {}
    if settlement.obligation_type == "CONTRIBUTION":
        if _money(settlement.amount_applied) > 0:
            expected["CONTRIBUTION_PAYMENT"] = _money(settlement.amount_applied)
    elif settlement.obligation_type == "LOAN_INSTALLMENT":
        if _money(settlement.interest_applied) > 0:
            expected["LOAN_INTEREST_PAYMENT"] = _money(settlement.interest_applied)
        if _money(settlement.penalty_applied) > 0:
            expected["LOAN_PENALTY_PAYMENT"] = _money(settlement.penalty_applied)
    elif settlement.obligation_type == "AGREEMENT_INSTALLMENT":
        if _money(settlement.amount_applied) > 0:
            expected["AGREEMENT_INSTALLMENT_PAYMENT"] = _money(settlement.amount_applied)
    else:
        raise ValueError("obligation_type sem contrato temporal")
    if len(rows) != len(expected):
        raise ValueError("conjunto de Ledger do settlement ausente, duplicado ou inesperado")
    by_type = {row.reference_type: row for row in rows}
    if len(by_type) != len(rows) or set(by_type) != set(expected):
        raise ValueError("tipos de Ledger do settlement incompatíveis")
    for kind, amount in expected.items():
        row = by_type[kind]
        if row.account != "CAIXINHA" or row.direction != "CREDIT" or _money(row.amount) != amount or row.reversal_of_id is not None:
            raise ValueError("Ledger do settlement incompatível")
        if row.hash_version is None:
            if row.financial_date is not None:
                raise ValueError("Ledger V1 do settlement possui financial_date")
        elif row.hash_version == 2:
            if row.financial_date != settlement.financial_date:
                raise ValueError("financial_date do Ledger V2 diverge do settlement")
        else:
            raise ValueError("versão de hash do Ledger desconhecida")
    return rows


def build_settlement_v6_snapshot(db: Session, payment: Payment, settlement: PaymentSettlement) -> dict[str, Any]:
    """Build the v6 payload without persisting it or changing a writer."""
    if settlement.receipt_version != SETTLEMENT_TEMPORAL_VERSION:
        raise ValueError("construção temporal exige receipt v6")
    expected_legacy_version = _SETTLEMENT_LEGACY_VERSION.get(settlement.obligation_type)
    if expected_legacy_version is None:
        raise ValueError("obligation_type sem contrato temporal")
    financial_date = _financial_date(settlement.financial_date)
    confirmed_at = _aware_utc(settlement.confirmed_at)
    if financial_date != _expected_financial_date(confirmed_at):
        raise ValueError("financial_date diverge de confirmed_at em America/Belem")
    if settlement.payment_id != payment.id:
        raise ValueError("PaymentSettlement não referencia o Payment")

    legacy_shape = copy.copy(settlement)
    legacy_shape.receipt_version = expected_legacy_version
    legacy_shape.receipt_number = f"PIX-{expected_legacy_version.upper()}-{payment.id:012d}"
    legacy = _receipt_snapshot(payment=payment, settlement=legacy_shape, ledger=_ledger_snapshot(db, payment.id))
    legacy["receipt_version"] = SETTLEMENT_TEMPORAL_VERSION
    legacy["receipt_number"] = f"PIX-V6-{payment.id:012d}"
    legacy["confirmation"]["confirmed_at"] = confirmed_at.isoformat()
    rows = _assert_settlement_ledger(db, payment, settlement)
    mfes = _settlement_mfes(db, payment, settlement)
    chain = verify_ledger_chain(db)
    if chain["status"] != "PASS":
        raise ValueError("cadeia de Ledger inválida")
    legacy["settlement"] = {"id": settlement.id, "financial_date": financial_date.isoformat()}
    legacy["financial_date"] = financial_date.isoformat()
    legacy["settlement_components"] = {
        "version": settlement.settlement_component_version,
        "normal_interest": format(_money(settlement.normal_interest_applied), "f") if settlement.normal_interest_applied is not None else None,
        "late_interest": format(_money(settlement.late_interest_applied), "f") if settlement.late_interest_applied is not None else None,
        "fixed_penalty": format(_money(settlement.fixed_penalty_applied), "f") if settlement.fixed_penalty_applied is not None else None,
    }
    if settlement.obligation_type == "CONTRIBUTION":
        obligation_row = db.get(Contribution, settlement.contribution_id)
        if obligation_row is None or obligation_row.member_id != settlement.member_id:
            raise ValueError("Contribution do settlement ausente ou incompatível")
        legacy["obligation_evidence"] = {
            "id": obligation_row.id, "member_id": obligation_row.member_id,
            "amount": format(_money(obligation_row.amount), "f"),
            "competence": obligation_row.competence.isoformat(),
            "due_date": obligation_row.due_date.isoformat() if obligation_row.due_date else None,
        }
    elif settlement.obligation_type == "LOAN_INSTALLMENT":
        obligation_row = db.get(LoanInstallment, settlement.loan_installment_id)
        if obligation_row is None:
            raise ValueError("LoanInstallment do settlement ausente")
        legacy["obligation_evidence"] = {
            "id": obligation_row.id, "loan_id": obligation_row.loan_id,
            "due_date": obligation_row.due_date.isoformat(),
            "amount": format(_money(obligation_row.amount), "f"),
        }
    else:
        obligation_row = db.get(AgreementInstallment, settlement.agreement_installment_id)
        if obligation_row is None:
            raise ValueError("AgreementInstallment do settlement ausente")
        legacy["obligation_evidence"] = {
            "id": obligation_row.id, "agreement_id": obligation_row.agreement_id,
            "due_date": obligation_row.due_date.isoformat(),
            "principal": format(_money(obligation_row.principal), "f"),
            "penalty_amount": format(_money(obligation_row.penalty_amount), "f"),
        }
    legacy["ledger_entries"] = [_ledger_evidence(row) for row in rows]
    legacy["member_financial_entries"] = [_mfe_evidence(row) for row in mfes]
    return legacy


def verify_settlement_v6(db: Session, payment: Payment, settlement: PaymentSettlement) -> tuple[bool, str]:
    if settlement.receipt_version != SETTLEMENT_TEMPORAL_VERSION:
        return False, "settlement não é receipt v6"
    if settlement.financial_date is None:
        return False, "settlement v6 sem financial_date"
    try:
        stored = json.loads(settlement.receipt_snapshot_json)
        canonical = _canonical_json(stored)
        if canonical != settlement.receipt_snapshot_json:
            return False, "receipt v6 não é canônico"
        if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != settlement.receipt_hash:
            return False, "hash do receipt v6 inválido"
        expected = build_settlement_v6_snapshot(db, payment, settlement)
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return False, str(exc) or "receipt v6 inválido"
    if stored != expected or settlement.receipt_number != expected["receipt_number"]:
        return False, "evidência temporal do settlement diverge do estado persistido"
    return True, ""


def build_reversal_v2_snapshot(
    db: Session,
    reversal: PaymentReversal,
    payment: Payment,
    settlement: PaymentSettlement,
    legacy_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Wrap the obligation-specific v1 reversal evidence in a temporal v2 envelope.

    The passed legacy payload is the already established obligation evidence;
    this helper is opt-in and is not called from the operational reversal path.
    """
    if reversal.receipt_version != REVERSAL_TEMPORAL_VERSION:
        raise ValueError("construção temporal exige receipt v2")
    financial_date = _financial_date(reversal.financial_date)
    reversed_at = _aware_utc(reversal.reversed_at)
    if financial_date != _expected_financial_date(reversed_at):
        raise ValueError("financial_date diverge de reversed_at em America/Belem")
    if legacy_evidence.get("receipt_version") != "v1":
        raise ValueError("evidência base da reversão deve ser v1")
    if reversal.payment_id != payment.id or reversal.settlement_id != settlement.id:
        raise ValueError("identidade Payment/Settlement da reversão incompatível")
    components = db.query(PaymentReversalComponent).filter(
        PaymentReversalComponent.payment_reversal_id == reversal.id
    ).order_by(PaymentReversalComponent.original_ledger_entry_id).all()
    ledger_rows = []
    for component in components:
        original = db.get(LedgerEntry, component.original_ledger_entry_id)
        compensating = db.get(LedgerEntry, component.compensating_ledger_entry_id)
        if original is None or compensating is None:
            raise ValueError("componente de Ledger da reversão incompleto")
        if original.hash_version not in (None, 2) or compensating.hash_version not in (None, 2):
            raise ValueError("versão de hash do Ledger da reversão desconhecida")
        if original.hash_version is None and original.financial_date is not None:
            raise ValueError("Ledger original V1 possui financial_date")
        if compensating.hash_version is None and compensating.financial_date is not None:
            raise ValueError("Ledger compensatório V1 possui financial_date")
        if compensating.hash_version == 2 and compensating.financial_date != financial_date:
            raise ValueError("financial_date do Ledger compensatório diverge da reversão")
        ledger_rows.extend((original, compensating))
    if verify_ledger_chain(db)["status"] != "PASS":
        raise ValueError("cadeia de Ledger inválida")
    mfe_query = db.query(MemberFinancialEntry)
    if settlement.obligation_type == "LOAN_INSTALLMENT":
        mfes = mfe_query.filter(
            MemberFinancialEntry.reference_type.in_(("LOAN_PRINCIPAL_PAYMENT", "PAYMENT_REVERSAL")),
            (MemberFinancialEntry.reference_id == str(payment.id))
            | (MemberFinancialEntry.payment_reversal_id == reversal.id),
        ).order_by(MemberFinancialEntry.id).all()
    elif settlement.obligation_type == "CONTRIBUTION":
        mfes = mfe_query.filter(
            (MemberFinancialEntry.payment_settlement_id == settlement.id)
            | (MemberFinancialEntry.payment_reversal_id == reversal.id)
        ).order_by(MemberFinancialEntry.id).all()
    elif settlement.obligation_type == "AGREEMENT_INSTALLMENT":
        mfes = []
    else:
        raise ValueError("obligation_type sem contrato temporal")
    return {
        "receipt_version": REVERSAL_TEMPORAL_VERSION,
        "receipt_number": f"PIX-REV-V2-{payment.id}",
        "financial_date": financial_date.isoformat(),
        "reversal": {
            "id": reversal.id,
            "payment_id": payment.id,
            "settlement_id": settlement.id,
            "reversed_at": reversed_at.isoformat(),
            "admin_id": reversal.admin_id,
            "reason": reversal.reason,
            "reversal_competence": reversal.reversal_competence.isoformat(),
        },
        "settlement": {
            "id": settlement.id,
            "receipt_number": settlement.receipt_number,
            "receipt_version": settlement.receipt_version,
            "receipt_hash": settlement.receipt_hash,
        },
        "amounts": {name: format(_money(getattr(reversal, name)), "f") for name in (
            "amount_received", "amount_applied", "principal_applied", "interest_applied", "penalty_applied", "excess_amount"
        )},
        "legacy_obligation_evidence": legacy_evidence,
        "ledger_entries": [_ledger_evidence(row) for row in sorted({row.id: row for row in ledger_rows}.values(), key=lambda item: item.id)],
        "member_financial_entries": [_mfe_evidence(row) for row in mfes],
    }


def verify_reversal_v2(db: Session, reversal: PaymentReversal) -> tuple[bool, str]:
    if reversal.receipt_version != REVERSAL_TEMPORAL_VERSION:
        return False, "reversal não é receipt v2"
    try:
        snapshot = json.loads(reversal.receipt_snapshot_json)
        canonical = _canonical_json(snapshot)
        if canonical != reversal.receipt_snapshot_json or hashlib.sha256(canonical.encode("utf-8")).hexdigest() != reversal.receipt_hash:
            return False, "canonical JSON/hash da reversão inválido"
        payment = db.get(Payment, reversal.payment_id)
        settlement = db.get(PaymentSettlement, reversal.settlement_id)
        if payment is None or settlement is None or settlement.payment_id != payment.id:
            return False, "Payment/Settlement da reversão inexistente ou incompatível"
        # Reconstruct the obligation evidence from the immutable v1 receipt
        # retained inside the authenticated temporal envelope.
        expected = build_reversal_v2_snapshot(
            db, reversal, payment, settlement, snapshot.get("legacy_obligation_evidence") or {}
        )
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return False, str(exc) or "receipt v2 inválido"
    if snapshot != expected or reversal.financial_date is None:
        return False, "evidência temporal da reversão diverge do estado persistido"
    if snapshot.get("receipt_number") != reversal.receipt_number:
        return False, "receipt_number da reversão incompatível"
    return True, ""
