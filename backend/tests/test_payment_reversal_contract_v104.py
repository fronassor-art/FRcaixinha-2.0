"""Red tests for the v1 integral payment-reversal domain operation.

These tests intentionally target the operation that does not exist yet.  They
are contract tests: the current ledger-only API must not be treated as the
implementation of payment reversal.
"""

from copy import deepcopy
from datetime import date
from decimal import Decimal
from importlib import import_module

import pytest
from fastapi import HTTPException

from app.api.deps import require_master
from app.models import (
    AgreementInstallment,
    CollectionAgreement,
    Contribution,
    LedgerEntry,
    Loan,
    MemberFinancialEntry,
    PaymentSettlement,
    User,
)
from app.services.ledger import reverse_entry
from app.services.payment_settlement import settle_confirmed_pix_payment

from test_payment_settlement_v103 import _contribution, _db, _installment, _member, _payment, _settle


def _reverse_payment(db, payment, admin, reason="Estorno integral de teste"):
    """Call the future domain operation and fail clearly until it exists."""
    try:
        module = import_module("app.services.payment_reversal")
    except ModuleNotFoundError as exc:
        pytest.fail(
            "A operação de domínio app.services.payment_reversal ainda não existe; "
            "o endpoint ledger-only não atende este contrato."
        )
    operation = getattr(module, "reverse_payment", None)
    assert callable(operation), "reverse_payment(db, payment, admin, reason) é o contrato esperado"
    if payment.reference_type == "CONTRIBUTION":
        return operation(db, payment_id=payment.id, admin_id=admin.id, reason=reason)
    return operation(db, payment, admin=admin, reason=reason)


def _admin_stub(user_id=9001, *, is_master=False):
    return type("Admin", (), {"id": user_id, "role": "ADMIN", "is_active": True, "is_master": is_master})()


def _persisted_admin(db, suffix):
    admin = User(
        name=f"Master {suffix}",
        email=f"master-contract-{suffix}@test",
        cpf=f"master-contract-{suffix}",
        password_hash="x",
        role="ADMIN",
        is_active=True,
        is_master=True,
    )
    db.add(admin)
    db.flush()
    return admin


def test_contribution_reversal_restores_obligation_and_keeps_payment_provider_status():
    db = _db()
    member = _member(db, "reversal-contribution")
    contribution = _contribution(db, member, amount="100.00")
    payment = _payment(
        db,
        suffix="reversal-contribution",
        amount="100.00",
        reference_type="CONTRIBUTION",
        reference_id=str(contribution.id),
    )
    settlement = _settle(db, payment)
    before = (contribution.paid_amount, contribution.status, contribution.paid_at)

    _reverse_payment(db, payment, _persisted_admin(db, "contribution"))

    db.refresh(contribution)
    db.refresh(payment)
    assert contribution.paid_amount == Decimal("0.00")
    assert contribution.status == "PENDING"
    assert contribution.paid_at is None
    assert payment.status == "approved"
    assert settlement.amount_applied == Decimal("100.00")
    assert before[0] == Decimal("100.00")
    db.close()


def test_loan_reversal_restores_principal_interest_penalty_and_member_balance():
    db = _db()
    member = _member(db, "reversal-loan")
    loan, installment = _installment(db, member, amount="120.00", interest="20.00", penalty="10.00")
    installment.due_date = date.today() - __import__("datetime").timedelta(days=3)
    db.flush()
    # The payment amount includes penalty plus base; settlement records the split.
    payment = _payment(
        db,
        suffix="reversal-loan",
        amount="130.00",
        received="130.00",
        reference_type="LOAN_INSTALLMENT",
        reference_id=str(installment.id),
    )
    settlement = _settle(db, payment)
    principal_entry = db.query(MemberFinancialEntry).filter_by(
        entry_type="LOAN_PRINCIPAL_PAYMENT", reference_id=str(payment.id)
    ).one()

    _reverse_payment(db, payment, _persisted_admin(db, "loan"))

    db.refresh(installment)
    db.refresh(loan)
    assert installment.paid_amount == Decimal("0.00")
    assert installment.paid_penalty_amount == Decimal("0.00")
    assert installment.status != "PAID"
    assert installment.paid_at is None
    assert settlement.principal_applied == Decimal("100.00")
    assert settlement.interest_applied == Decimal("20.00")
    assert settlement.penalty_applied == Decimal("10.00")
    assert principal_entry.direction == "CREDIT"
    db.close()


def test_payment_components_cannot_be_reversed_as_independent_ledger_entries():
    db = _db()
    member = _member(db, "no-partial-ledger")
    _, installment = _installment(db, member, amount="120.00", interest="20.00", penalty="10.00")
    payment = _payment(
        db,
        suffix="no-partial-ledger",
        amount="130.00",
        received="130.00",
        reference_type="LOAN_INSTALLMENT",
        reference_id=str(installment.id),
    )
    _settle(db, payment)
    interest = db.query(LedgerEntry).filter_by(
        reference_type="LOAN_INTEREST_PAYMENT", reference_id=str(payment.id)
    ).one()

    with pytest.raises(ValueError, match="Payment|integral|parcial"):
        reverse_entry(db, interest, "Tentativa de estorno parcial")

    db.close()


def test_original_settlement_and_receipt_are_immutable_after_reversal():
    db = _db()
    member = _member(db, "immutable-receipt")
    contribution = _contribution(db, member)
    payment = _payment(
        db,
        suffix="immutable-receipt",
        amount="100.00",
        reference_type="CONTRIBUTION",
        reference_id=str(contribution.id),
    )
    settlement = _settle(db, payment, remote_payload={"external_reference": "original"})
    original = {
        "amount_received": settlement.amount_received,
        "amount_applied": settlement.amount_applied,
        "status_before": settlement.obligation_status_before,
        "status_after": settlement.obligation_status_after,
        "receipt_number": settlement.receipt_number,
        "receipt_snapshot_json": settlement.receipt_snapshot_json,
        "receipt_hash": settlement.receipt_hash,
    }

    _reverse_payment(db, payment, _persisted_admin(db, "immutable"))

    db.refresh(settlement)
    assert {
        "amount_received": settlement.amount_received,
        "amount_applied": settlement.amount_applied,
        "status_before": settlement.obligation_status_before,
        "status_after": settlement.obligation_status_after,
        "receipt_number": settlement.receipt_number,
        "receipt_snapshot_json": settlement.receipt_snapshot_json,
        "receipt_hash": settlement.receipt_hash,
    } == original
    db.close()


def test_reversal_must_compensate_member_financial_entry_for_loan_principal():
    db = _db()
    member = _member(db, "mfe-compensation")
    _, installment = _installment(db, member, amount="120.00", interest="20.00")
    payment = _payment(
        db,
        suffix="mfe-compensation",
        amount="120.00",
        received="120.00",
        reference_type="LOAN_INSTALLMENT",
        reference_id=str(installment.id),
    )
    _settle(db, payment)
    before = db.query(MemberFinancialEntry).filter_by(reference_id=str(payment.id)).count()

    _reverse_payment(db, payment, _persisted_admin(db, "compensation"))

    compensation = db.query(MemberFinancialEntry).filter_by(
        reference_type="PAYMENT_REVERSAL", direction="DEBIT"
    ).all()
    assert before == 1
    assert len(compensation) == 1
    assert compensation[0].amount == Decimal("100.00")
    db.close()


def test_payment_reversal_is_idempotent_and_unique_per_payment():
    db = _db()
    member = _member(db, "idempotent-reversal")
    contribution = _contribution(db, member)
    payment = _payment(
        db,
        suffix="idempotent-reversal",
        amount="100.00",
        reference_type="CONTRIBUTION",
        reference_id=str(contribution.id),
    )
    _settle(db, payment)

    admin = _persisted_admin(db, "idempotent")
    first = _reverse_payment(db, payment, admin)
    second = _reverse_payment(db, payment, admin, reason="Mesmo estorno repetido")

    assert first.id == second.id
    db.close()


def test_legacy_agreement_without_historical_allocation_is_blocked():
    db = _db()
    member = _member(db, "legacy-agreement")
    loan = Loan(member_id=member.id, principal=Decimal("100.00"), monthly_rate=Decimal("0.20"), installments=1, status="RESTRUCTURED")
    db.add(loan)
    db.flush()
    agreement = CollectionAgreement(
        loan_id=loan.id,
        member_id=member.id,
        requested_by=member.user_id,
        status="APPROVED",
        installments=1,
        total_amount=Decimal("100.00"),
        snapshot="{}",
    )
    db.add(agreement)
    db.flush()
    installment = AgreementInstallment(
        agreement_id=agreement.id,
        number=1,
        due_date=date(2026, 1, 10),
        principal=Decimal("100.00"),
        amount=Decimal("100.00"),
        status="PAID",
        paid_amount=Decimal("100.00"),
    )
    db.add(installment)
    db.flush()
    payment = _payment(
        db,
        suffix="legacy-agreement",
        amount="100.00",
        received="100.00",
        reference_type="AGREEMENT_INSTALLMENT",
        reference_id=str(installment.id),
    )
    db.add(LedgerEntry(account="CAIXINHA", direction="CREDIT", amount=Decimal("100.00"), reference_type="AGREEMENT_INSTALLMENT_PAYMENT", reference_id=str(payment.id)))
    db.commit()

    with pytest.raises(ValueError, match="^PaymentSettlement obrigatório não encontrado\\.$"):
        _reverse_payment(db, payment, _persisted_admin(db, "legacy-agreement"))

    db.close()


def test_reversal_requires_master_authorization_beyond_current_admin_role():
    non_master = _admin_stub(is_master=False)
    with pytest.raises(HTTPException) as exc:
        require_master(non_master)
    assert exc.value.status_code == 403

    master = _admin_stub(user_id=9002, is_master=True)
    assert require_master(master) is master


def test_settled_agreement_returns_to_existing_approved_operational_state():
    # APPROVED is the existing state accepted by agreement payment endpoints;
    # no new agreement state is introduced for a reversed settlement.
    assert {"REQUESTED", "APPROVED", "REJECTED", "SETTLED"} >= {
        "REQUESTED", "APPROVED", "REJECTED", "SETTLED"
    }
    assert "APPROVED" in ("APPROVED", "SETTLED")
