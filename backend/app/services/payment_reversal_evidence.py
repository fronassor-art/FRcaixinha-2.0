"""Read-only validation of the economic effect of payment reversals."""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.models import (
    AgreementInstallment,
    CollectionAgreement,
    Contribution,
    LedgerEntry,
    Loan,
    LoanInstallment,
    MemberFinancialAccount,
    MemberFinancialEntry,
    Payment,
    PaymentReversal,
    PaymentReversalComponent,
    PaymentSettlement,
)
from app.services.payment_settlement import _canonical_json


CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def _utc(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value):
    normalized = _utc(value)
    return normalized.isoformat() if normalized is not None else None


def _assert_common_snapshot(snapshot, reversal, payment, settlement):
    if snapshot.get("payment", {}).get("id") != payment.id or snapshot.get("payment", {}).get("status") != payment.status:
        return False, "Payment do receipt do reversal incompatível"
    payment_snapshot = snapshot.get("payment", {})
    for field in ("reference_type", "reference_id"):
        if field in payment_snapshot and payment_snapshot[field] != getattr(payment, field):
            return False, f"{field} do Payment incompatível"
    settlement_snapshot = snapshot.get("settlement", {})
    if settlement_snapshot.get("id") != settlement.id:
        return False, "Settlement do receipt do reversal incompatível"
    for field in ("receipt_number", "receipt_version", "receipt_hash"):
        if field in settlement_snapshot and settlement_snapshot[field] != getattr(settlement, field):
            return False, f"{field} do Settlement incompatível"
    reversal_snapshot = snapshot.get("reversal", {})
    if reversal_snapshot.get("reversed_at") != _iso(reversal.reversed_at):
        return False, "reversed_at do receipt incompatível"
    if reversal_snapshot.get("reversal_competence") != reversal.reversal_competence.isoformat():
        return False, "reversal_competence do receipt incompatível"
    if reversal_snapshot.get("id") != reversal.id or reversal_snapshot.get("admin_id") != reversal.admin_id or reversal_snapshot.get("reason") != reversal.reason:
        return False, "metadados do reversal incompatíveis"
    expected_amounts = {
        name: format(_money(getattr(reversal, name)), "f")
        for name in ("amount_received", "amount_applied", "principal_applied", "interest_applied", "penalty_applied", "excess_amount")
    }
    if snapshot.get("amounts") != expected_amounts:
        return False, "valores do receipt do reversal incompatíveis"
    if any(_money(getattr(reversal, name)) != _money(getattr(settlement, name)) for name in expected_amounts):
        return False, "valores do reversal divergem do Settlement"
    return True, ""


def _receipt_valid(reversal: PaymentReversal) -> tuple[bool, dict | None, str]:
    try:
        snapshot = json.loads(reversal.receipt_snapshot_json)
        canonical = _canonical_json(snapshot)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False, None, "receipt do reversal ilegível"
    if reversal.receipt_snapshot_json != canonical:
        return False, None, "receipt do reversal não é canônico"
    if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != reversal.receipt_hash:
        return False, None, "hash do reversal inválido"
    if snapshot.get("receipt_version") != reversal.receipt_version or snapshot.get("receipt_number") != reversal.receipt_number:
        return False, None, "identidade do receipt do reversal incompatível"
    metadata = snapshot.get("reversal") or {}
    if metadata.get("id") != reversal.id or metadata.get("admin_id") != reversal.admin_id or metadata.get("reason") != reversal.reason:
        return False, None, "metadados do reversal incompatíveis"
    return True, snapshot, ""


def _settlement_valid(payment: Payment, settlement: PaymentSettlement) -> tuple[bool, str]:
    if settlement is None or settlement.payment_id != payment.id:
        return False, "settlement não referencia o Payment"
    if settlement.obligation_type not in {"CONTRIBUTION", "LOAN_INSTALLMENT", "AGREEMENT_INSTALLMENT"}:
        return False, "obligation_type incompatível"
    if settlement.receipt_snapshot_json is None or settlement.receipt_hash is None:
        return False, "settlement sem receipt/hash"
    try:
        snapshot = json.loads(settlement.receipt_snapshot_json)
        canonical = _canonical_json(snapshot)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False, "receipt do settlement ilegível"
    if canonical != settlement.receipt_snapshot_json or hashlib.sha256(canonical.encode("utf-8")).hexdigest() != settlement.receipt_hash:
        return False, "receipt/hash do settlement inválido"
    if (snapshot.get("payment") or {}).get("id") != payment.id:
        return False, "Payment ausente ou incompatível no settlement"
    return True, ""


def _component_chain(db: Session, reversal: PaymentReversal, expected: list[LedgerEntry]) -> tuple[bool, str]:
    components = db.query(PaymentReversalComponent).filter(
        PaymentReversalComponent.payment_reversal_id == reversal.id
    ).all()
    if len(components) != len(expected):
        return False, "componentes do reversal ausentes ou duplicados"
    by_original = {component.original_ledger_entry_id: component for component in components}
    if len(by_original) != len(components):
        return False, "componentes do reversal duplicados"
    for original in expected:
        component = by_original.get(original.id)
        if component is None:
            return False, "component do Ledger original ausente"
        compensating = db.get(LedgerEntry, component.compensating_ledger_entry_id)
        if compensating is None or compensating.id == original.id:
            return False, "Ledger compensatório ausente ou igual ao original"
        compensating_rows = db.query(LedgerEntry).filter(
            LedgerEntry.reversal_of_id == original.id
        ).all()
        if len(compensating_rows) != 1 or compensating_rows[0].id != compensating.id:
            return False, "Ledger compensatório ausente, duplicado ou incompatível"
        if compensating.account != original.account or compensating.direction == original.direction:
            return False, "Ledger compensatório incompatível"
        if _money(compensating.amount) != _money(original.amount):
            return False, "valor do Ledger compensatório incompatível"
        if compensating.reference_type != "REVERSAL" or compensating.reference_id != str(original.id):
            return False, "referência do Ledger compensatório incompatível"
        if compensating.reversal_of_id != original.id:
            return False, "reversal_of_id incompatível"
        if db.query(PaymentReversalComponent).filter(
            PaymentReversalComponent.original_ledger_entry_id == original.id
        ).count() != 1 or db.query(PaymentReversalComponent).filter(
            PaymentReversalComponent.compensating_ledger_entry_id == compensating.id
        ).count() != 1:
            return False, "vínculo do componente não é único"
    return True, ""


def _payment_ledger_set_is_exact(db, payment, expected):
    expected_ids = {row.id for row in expected}
    rows = db.query(LedgerEntry).filter(LedgerEntry.reference_id == str(payment.id)).all()
    for row in rows:
        if row.id not in expected_ids and row.reversal_of_id not in expected_ids:
            return False, "Ledger payment-linked extra ou incompatível"
    return True, ""


def _receipt_components_match(db: Session, reversal, snapshot, expected):
    components = db.query(PaymentReversalComponent).filter(
        PaymentReversalComponent.payment_reversal_id == reversal.id
    ).all()
    expected_pairs = {(row.id, component.compensating_ledger_entry_id) for row in expected for component in components if component.original_ledger_entry_id == row.id}
    listed = snapshot.get("ledger_components")
    if listed is not None:
        actual_pairs = {(item.get("original_ledger_entry_id"), item.get("compensating_ledger_entry_id")) for item in listed}
        if actual_pairs != expected_pairs:
            return False, "componentes do receipt do reversal incompatíveis"
        return True, ""
    single = snapshot.get("ledger_component")
    if len(expected) != 1 or not isinstance(single, dict):
        return False, "componentes ausentes no receipt do reversal"
    pair = next(iter(expected_pairs), None)
    if pair is None or (single.get("original_ledger_entry_id"), single.get("compensating_ledger_entry_id")) != pair:
        return False, "component do receipt do reversal incompatível"
    return True, ""


def _validate_contribution(db, payment, settlement, reversal, snapshot):
    if settlement.receipt_version != "v1" or settlement.contribution_id is None:
        return False, "settlement Contribution não é v1"
    contribution = db.get(Contribution, settlement.contribution_id)
    if contribution is None or settlement.member_id != contribution.member_id:
        return False, "Contribution do settlement inexistente ou incompatível"
    if reversal.original_date_kind != "CONTRIBUTION_COMPETENCE" or reversal.original_competence != contribution.competence or reversal.original_due_date is not None:
        return False, "competência original da Contribution incompatível"
    obligation = snapshot.get("obligation") or {}
    if obligation.get("type") != "CONTRIBUTION" or obligation.get("contribution_id") != contribution.id:
        return False, "obrigação do receipt Contribution incompatível"
    if obligation.get("original_competence") != contribution.competence.isoformat():
        return False, "competência original da Contribution incompatível"
    if obligation.get("state_before") is None or obligation.get("state_after") is None:
        return False, "estado da Contribution ausente no receipt"
    rows = db.query(LedgerEntry).filter(
        LedgerEntry.reference_type == "CONTRIBUTION_PAYMENT",
        LedgerEntry.reference_id == str(payment.id),
        LedgerEntry.reversal_of_id.is_(None),
    ).all()
    if len(rows) != 1:
        return False, "Ledger original Contribution ausente ou duplicado"
    original = rows[0]
    if original.account != "CAIXINHA" or original.direction != "CREDIT" or _money(original.amount) != _money(settlement.amount_applied):
        return False, "Ledger original Contribution incompatível"
    ok, detail = _payment_ledger_set_is_exact(db, payment, [original])
    if not ok:
        return False, detail
    amounts = snapshot.get("amounts") or {}
    if amounts.get("amount_applied") != format(_money(settlement.amount_applied), "f"):
        return False, "valor do receipt Contribution incompatível"
    ok, detail = _component_chain(db, reversal, [original])
    return (ok, detail) if not ok else _receipt_components_match(db, reversal, snapshot, [original])


def _validate_loan(db, payment, settlement, reversal, snapshot):
    if settlement.receipt_version != "v4" or settlement.loan_installment_id is None:
        return False, "settlement Loan não é v4"
    installment = db.get(LoanInstallment, settlement.loan_installment_id)
    if installment is None:
        return False, "LoanInstallment do settlement inexistente"
    if reversal.original_date_kind != "LOAN_INSTALLMENT_DUE_DATE" or reversal.original_due_date != installment.due_date or reversal.original_competence is not None:
        return False, "due_date original do Loan incompatível"
    loan = db.get(Loan, installment.loan_id)
    if loan is None:
        return False, "Loan do settlement inexistente"
    obligation = snapshot.get("loan_installment") or {}
    if obligation.get("id") != installment.id or obligation.get("due_date") != installment.due_date.isoformat():
        return False, "LoanInstallment do receipt incompatível"
    expected = []
    for reference_type, amount in (
        ("LOAN_INTEREST_PAYMENT", settlement.interest_applied),
        ("LOAN_PENALTY_PAYMENT", settlement.penalty_applied),
    ):
        rows = db.query(LedgerEntry).filter(
            LedgerEntry.reference_type == reference_type,
            LedgerEntry.reference_id == str(payment.id),
            LedgerEntry.reversal_of_id.is_(None),
        ).all()
        if _money(amount) > ZERO:
            if len(rows) != 1 or rows[0].account != "CAIXINHA" or rows[0].direction != "CREDIT" or _money(rows[0].amount) != _money(amount):
                return False, f"Ledger original Loan {reference_type} ausente ou incompatível"
            expected.append(rows[0])
        elif rows:
            return False, f"Ledger Loan inesperado para {reference_type}"
    ok, detail = _payment_ledger_set_is_exact(db, payment, expected)
    if not ok:
        return False, detail
    loan_snapshot = snapshot.get("loan") or {}
    if loan_snapshot.get("id") != loan.id or loan_snapshot.get("status_before") != settlement.loan_status_before or loan_snapshot.get("status_after") != settlement.loan_status_after or loan_snapshot.get("state_revision_before") != settlement.loan_state_revision_before or loan_snapshot.get("state_revision_after") != settlement.loan_state_revision_after or loan_snapshot.get("paid_at_before") != _iso(settlement.loan_paid_at_before) or loan_snapshot.get("paid_at_after") != _iso(settlement.loan_paid_at_after):
        return False, "estado do Loan no receipt incompatível"
    installment_snapshot = snapshot.get("loan_installment") or {}
    if installment_snapshot.get("status_before") != settlement.loan_installment_status_before or installment_snapshot.get("status_after") != settlement.loan_installment_status_after or installment_snapshot.get("paid_at_before") != _iso(settlement.loan_installment_paid_at_before) or installment_snapshot.get("paid_at_after") != _iso(settlement.loan_installment_paid_at_after):
        return False, "estado da LoanInstallment no receipt incompatível"
    member_financial = snapshot.get("member_financial") or {}
    if _money(settlement.principal_applied) > ZERO:
        entries = db.query(MemberFinancialEntry).filter(
            MemberFinancialEntry.entry_type == "LOAN_PRINCIPAL_PAYMENT",
            MemberFinancialEntry.reference_type == "LOAN_PRINCIPAL_PAYMENT",
            MemberFinancialEntry.reference_id == str(payment.id),
        ).join(MemberFinancialAccount, MemberFinancialEntry.account_id == MemberFinancialAccount.id).filter(
            MemberFinancialAccount.member_id == settlement.member_id,
        ).all()
        matches = [entry for entry in entries if _money(entry.amount) == _money(settlement.principal_applied) and entry.direction == "CREDIT"]
        if len(entries) != 1 or len(matches) != 1:
            return False, "MFE original de principal Loan ausente ou incompatível"
        compensations = db.query(MemberFinancialEntry).join(
            MemberFinancialAccount, MemberFinancialEntry.account_id == MemberFinancialAccount.id
        ).filter(
            MemberFinancialEntry.entry_type == "LOAN_PRINCIPAL_REVERSAL",
            MemberFinancialEntry.direction == "DEBIT",
            MemberFinancialEntry.reference_type == "PAYMENT_REVERSAL",
            MemberFinancialEntry.reference_id == str(reversal.id),
            MemberFinancialEntry.payment_reversal_id == reversal.id,
            MemberFinancialAccount.member_id == settlement.member_id,
        ).all()
        if len(compensations) != 1 or _money(compensations[0].amount) != _money(settlement.principal_applied):
            return False, "MFE compensatória de principal Loan ausente ou incompatível"
    elif db.query(MemberFinancialEntry).filter(
        MemberFinancialEntry.entry_type == "LOAN_PRINCIPAL_REVERSAL",
        MemberFinancialEntry.payment_reversal_id == reversal.id,
    ).count():
        return False, "MFE compensatória inesperada para principal zero"
    elif member_financial.get("original_entry_id") is not None or member_financial.get("compensating_entry_id") is not None:
        return False, "MFE indevida para principal zero"
    ok, detail = _component_chain(db, reversal, expected)
    return (ok, detail) if not ok else _receipt_components_match(db, reversal, snapshot, expected)


def _validate_agreement(db, payment, settlement, reversal, snapshot):
    if settlement.receipt_version != "v5" or settlement.agreement_installment_id is None:
        return False, "settlement Agreement não é v5"
    installment = db.get(AgreementInstallment, settlement.agreement_installment_id)
    agreement = db.get(CollectionAgreement, installment.agreement_id) if installment else None
    if installment is None or agreement is None or settlement.member_id != agreement.member_id:
        return False, "obrigação Agreement inexistente ou incompatível"
    if reversal.original_date_kind != "AGREEMENT_INSTALLMENT_DUE_DATE" or reversal.original_due_date != installment.due_date or reversal.original_competence is not None:
        return False, "due_date original do Agreement incompatível"
    obligation = snapshot.get("obligation") or {}
    if obligation != {
        "type": "AGREEMENT_INSTALLMENT",
        "member_id": settlement.member_id,
        "agreement_installment_id": installment.id,
        "collection_agreement_id": agreement.id,
    }:
        return False, "obrigação do receipt Agreement incompatível"
    rows = db.query(LedgerEntry).filter(
        LedgerEntry.reference_type == "AGREEMENT_INSTALLMENT_PAYMENT",
        LedgerEntry.reference_id == str(payment.id),
        LedgerEntry.reversal_of_id.is_(None),
    ).all()
    if len(rows) != 1 or rows[0].account != "CAIXINHA" or rows[0].direction != "CREDIT" or _money(rows[0].amount) != _money(settlement.amount_applied):
        return False, "Ledger original Agreement ausente ou incompatível"
    ok, detail = _payment_ledger_set_is_exact(db, payment, rows)
    if not ok:
        return False, detail
    agreement_snapshot = snapshot.get("agreement") or {}
    if agreement_snapshot.get("id") != agreement.id or agreement_snapshot.get("status_before") != settlement.collection_agreement_status_before or agreement_snapshot.get("status_after") != settlement.collection_agreement_status_after:
        return False, "estado do Agreement no receipt incompatível"
    installment_snapshot = snapshot.get("agreement_installment") or {}
    if installment_snapshot.get("id") != installment.id or installment_snapshot.get("due_date") != installment.due_date.isoformat() or installment_snapshot.get("status_before") != settlement.agreement_installment_status_before or installment_snapshot.get("status_after") != settlement.agreement_installment_status_after:
        return False, "estado da AgreementInstallment no receipt incompatível"
    ok, detail = _component_chain(db, reversal, rows)
    return (ok, detail) if not ok else _receipt_components_match(db, reversal, snapshot, rows)


def validate_reversal_effect(db: Session, reversal: PaymentReversal) -> tuple[bool, str]:
    """Return whether *reversal* completely neutralizes its original payment.

    This function is deliberately read-only: it performs no lock, flush, or
    mutation.  Callers must treat a false result as an audit finding, never as
    permission to net the original entry.
    """
    with db.no_autoflush:
        payment = db.get(Payment, reversal.payment_id)
        settlement = db.get(PaymentSettlement, reversal.settlement_id)
        if payment is None or settlement is None:
            return False, "Payment/Settlement original inexistente"
        if settlement.payment_id != payment.id:
            return False, "PaymentSettlement não referencia o Payment"
        ok, detail = _settlement_valid(payment, settlement)
        if not ok:
            return False, detail
        ok, snapshot, detail = _receipt_valid(reversal)
        if not ok:
            return False, detail
        ok, detail = _assert_common_snapshot(snapshot, reversal, payment, settlement)
        if not ok:
            return False, detail
        if reversal.payment_id != payment.id or reversal.settlement_id != settlement.id:
            return False, "PaymentReversal não referencia exatamente Payment/Settlement"
        if payment.status != "approved":
            return False, "Payment não está approved"
        if settlement.obligation_type == "CONTRIBUTION":
            return _validate_contribution(db, payment, settlement, reversal, snapshot)
        if settlement.obligation_type == "LOAN_INSTALLMENT":
            return _validate_loan(db, payment, settlement, reversal, snapshot)
        if settlement.obligation_type == "AGREEMENT_INSTALLMENT":
            return _validate_agreement(db, payment, settlement, reversal, snapshot)
        return False, "obligation_type sem validador"
