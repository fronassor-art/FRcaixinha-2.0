import asyncio
import hashlib
import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import (
    AgreementInstallment,
    CollectionAgreement,
    Group,
    LedgerEntry,
    Loan,
    Member,
    MemberFinancialEntry,
    Payment,
    PaymentSettlement,
    User,
)
from app.services.payment_settlement import settle_confirmed_pix_payment


def _db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _agreement(db, *, principal="100.00", penalty="0.00", paid="0.00", paid_penalty="0.00", status="OPEN", installments=1):
    suffix = str(db.query(User).count() + 1)
    group = Group(name=f"Agreement settlement {suffix}", max_installments=6)
    user = User(name=f"Member {suffix}", email=f"agreement-settlement-{suffix}@test", cpf=f"agreement-settlement-{suffix}", password_hash="x")
    db.add_all([group, user])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id)
    db.add(member)
    db.flush()
    loan = Loan(member_id=member.id, principal=Decimal("100.00"), monthly_rate=Decimal("0.20"), installments=1, status="RESTRUCTURED")
    db.add(loan)
    db.flush()
    agreement = CollectionAgreement(
        loan_id=loan.id,
        member_id=member.id,
        requested_by=user.id,
        status="APPROVED",
        installments=installments,
        total_amount=Decimal(principal) + Decimal(penalty),
        snapshot="{}",
    )
    db.add(agreement)
    db.flush()
    rows = []
    for number in range(1, installments + 1):
        row = AgreementInstallment(
            agreement_id=agreement.id,
            number=number,
            due_date=date(2026, 1, 10),
            principal=Decimal(principal) if number == 1 else Decimal("0.00"),
            penalty_amount=Decimal(penalty) if number == 1 else Decimal("0.00"),
            amount=Decimal(principal) + Decimal(penalty) if number == 1 else Decimal("0.00"),
            paid_amount=Decimal(paid) if number == 1 else Decimal("0.00"),
            paid_penalty_amount=Decimal(paid_penalty) if number == 1 else Decimal("0.00"),
            status=status if number == 1 else "OPEN",
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return member, agreement, rows


def _payment(db, member, installment, *, amount, received=None, suffix="payment"):
    payment = Payment(
        provider="mercado_pago",
        provider_payment_id=f"provider-{suffix}",
        idempotency_key=f"idempotency-{suffix}",
        amount=Decimal(amount),
        amount_received=Decimal(received if received is not None else amount),
        status="approved",
        raw_status="approved",
        reference_type="AGREEMENT_INSTALLMENT",
        reference_id=str(installment.id),
    )
    db.add(payment)
    db.flush()
    return payment


def _settle(db, payment):
    settlement = settle_confirmed_pix_payment(db, payment, confirmation_source="TEST")
    db.commit()
    return settlement


def test_full_agreement_payment_without_penalty_creates_settlement_and_ledger():
    db = _db()
    member, agreement, rows = _agreement(db)
    payment = _payment(db, member, rows[0], amount="100.00", suffix="full")

    settlement = _settle(db, payment)

    assert settlement.obligation_type == "AGREEMENT_INSTALLMENT"
    assert settlement.agreement_installment_id == rows[0].id
    assert settlement.amount_received == Decimal("100.00")
    assert settlement.amount_applied == Decimal("100.00")
    assert settlement.principal_applied == Decimal("100.00")
    assert settlement.penalty_applied == Decimal("0.00")
    assert settlement.interest_applied == Decimal("0.00")
    assert settlement.excess_amount == Decimal("0.00")
    assert settlement.obligation_status_before == "OPEN"
    assert settlement.obligation_status_after == "PAID"
    assert rows[0].status == "PAID"
    assert rows[0].paid_at is not None
    assert agreement.status == "SETTLED"
    ledger = db.query(LedgerEntry).filter_by(reference_id=str(payment.id)).one()
    assert ledger.reference_type == "AGREEMENT_INSTALLMENT_PAYMENT"
    assert ledger.direction == "CREDIT"
    assert ledger.account == "CAIXINHA"
    assert ledger.amount == settlement.amount_applied
    assert db.query(MemberFinancialEntry).count() == 0


def test_partial_payment_applies_penalty_before_principal():
    db = _db()
    _, agreement, rows = _agreement(db, principal="100.00", penalty="10.00")
    payment = _payment(db, None, rows[0], amount="15.00", suffix="penalty-first")

    settlement = _settle(db, payment)

    assert settlement.penalty_applied == Decimal("10.00")
    assert settlement.principal_applied == Decimal("5.00")
    assert settlement.amount_applied == Decimal("15.00")
    assert rows[0].paid_penalty_amount == Decimal("10.00")
    assert rows[0].paid_amount == Decimal("5.00")
    assert rows[0].status == "PARTIAL"
    assert agreement.status == "APPROVED"


@pytest.mark.parametrize(
    ("paid", "paid_penalty", "received", "expected_principal", "expected_penalty", "before", "after"),
    [
        ("10.00", "2.00", "5.00", "0.00", "5.00", "PARTIAL", "PARTIAL"),
        ("40.00", "0.00", "20.00", "10.00", "10.00", "PARTIAL", "PARTIAL"),
        ("40.00", "5.00", "20.00", "15.00", "5.00", "PARTIAL", "PARTIAL"),
        ("90.00", "10.00", "10.00", "10.00", "0.00", "PARTIAL", "PAID"),
    ],
)
def test_cumulative_agreement_allocation_preserves_prior_components(paid, paid_penalty, received, expected_principal, expected_penalty, before, after):
    db = _db()
    _, _, rows = _agreement(db, principal="100.00", penalty="10.00", paid=paid, paid_penalty=paid_penalty, status="PARTIAL")
    payment = _payment(db, None, rows[0], amount=received, suffix=f"cumulative-{paid}-{paid_penalty}-{received}")

    settlement = _settle(db, payment)

    assert settlement.obligation_status_before == before
    assert settlement.obligation_status_after == after
    assert settlement.principal_applied == Decimal(expected_principal)
    assert settlement.penalty_applied == Decimal(expected_penalty)
    assert settlement.interest_applied == Decimal("0.00")
    assert settlement.amount_applied == settlement.principal_applied + settlement.penalty_applied


def test_excess_is_recorded_without_ledger_excess_component():
    db = _db()
    _, _, rows = _agreement(db, principal="40.00", penalty="10.00")
    payment = _payment(db, None, rows[0], amount="70.00", suffix="excess")

    settlement = _settle(db, payment)

    assert settlement.amount_applied == Decimal("50.00")
    assert settlement.excess_amount == Decimal("20.00")
    assert db.query(LedgerEntry).filter_by(reference_id=str(payment.id)).one().amount == Decimal("50.00")


def test_status_transitions_and_collection_agreement_settles_only_after_all_installments():
    db = _db()
    _, agreement, rows = _agreement(db, principal="100.00", installments=2)
    rows[1].principal = Decimal("100.00")
    rows[1].amount = Decimal("100.00")
    db.flush()
    first = _payment(db, None, rows[0], amount="100.00", suffix="multi-first")
    first_settlement = _settle(db, first)
    assert first_settlement.obligation_status_before == "OPEN"
    assert first_settlement.obligation_status_after == "PAID"
    assert agreement.status == "APPROVED"

    second = _payment(db, None, rows[1], amount="100.00", suffix="multi-second")
    _settle(db, second)
    assert agreement.status == "SETTLED"


def test_receipt_contains_agreement_reference_and_is_canonical_and_hashed():
    db = _db()
    _, _, rows = _agreement(db)
    payment = _payment(db, None, rows[0], amount="25.00", suffix="receipt")

    settlement = _settle(db, payment)

    snapshot = json.loads(settlement.receipt_snapshot_json)
    assert snapshot["obligation"]["agreement_installment_id"] == rows[0].id
    assert json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False) == settlement.receipt_snapshot_json
    assert hashlib.sha256(settlement.receipt_snapshot_json.encode()).hexdigest() == settlement.receipt_hash


def test_same_payment_is_idempotent_for_obligation_ledger_settlement_and_receipt():
    db = _db()
    _, _, rows = _agreement(db)
    payment = _payment(db, None, rows[0], amount="25.00", suffix="idempotent")

    first = _settle(db, payment)
    original = (first.id, first.receipt_snapshot_json, first.receipt_hash, rows[0].paid_amount, rows[0].paid_penalty_amount)
    ledger_count = db.query(LedgerEntry).count()
    second = settle_confirmed_pix_payment(db, payment, confirmation_source="RETRY")
    db.commit()

    assert (second.id, second.receipt_snapshot_json, second.receipt_hash, rows[0].paid_amount, rows[0].paid_penalty_amount) == original
    assert db.query(PaymentSettlement).filter_by(payment_id=payment.id).count() == 1
    assert db.query(LedgerEntry).count() == ledger_count


def test_legacy_agreement_with_ledger_is_not_backfilled_or_reapplied():
    db = _db()
    _, _, rows = _agreement(db, principal="100.00", penalty="10.00")
    payment = _payment(db, None, rows[0], amount="40.00", suffix="legacy")
    payment.ledger_posted_at = payment.created_at
    db.add(LedgerEntry(account="CAIXINHA", direction="CREDIT", amount=Decimal("40.00"), reference_type="AGREEMENT_INSTALLMENT_PAYMENT", reference_id=str(payment.id)))
    db.commit()
    before = (rows[0].paid_amount, rows[0].paid_penalty_amount, rows[0].status)

    with pytest.raises(ValueError, match="legado"):
        settle_confirmed_pix_payment(db, payment, confirmation_source="TEST")
    db.rollback()

    assert (rows[0].paid_amount, rows[0].paid_penalty_amount, rows[0].status) == before
    assert db.query(PaymentSettlement).filter_by(payment_id=payment.id).count() == 0
    assert db.query(LedgerEntry).filter_by(reference_id=str(payment.id)).count() == 1


def test_invalid_agreement_reference_is_rejected_without_financial_changes():
    db = _db()
    member, _, rows = _agreement(db)
    payment = _payment(db, member, rows[0], amount="10.00", suffix="invalid-reference")
    payment.reference_id = "not-a-number"
    db.commit()

    with pytest.raises(ValueError):
        settle_confirmed_pix_payment(db, payment, confirmation_source="TEST")
    db.rollback()

    assert db.query(PaymentSettlement).count() == 0
    assert db.query(LedgerEntry).count() == 0


def test_rollback_removes_obligation_ledger_and_settlement_together():
    db = _db()
    _, _, rows = _agreement(db, principal="100.00", penalty="10.00")
    payment = _payment(db, None, rows[0], amount="15.00", suffix="rollback")
    db.commit()
    db.refresh(rows[0])

    settle_confirmed_pix_payment(db, payment, confirmation_source="TEST")
    assert rows[0].paid_amount == Decimal("5.00")
    assert db.query(LedgerEntry).count() == 1
    assert db.query(PaymentSettlement).count() == 1
    db.rollback()
    db.expire_all()

    assert rows[0].paid_amount == Decimal("0.00")
    assert rows[0].paid_penalty_amount == Decimal("0.00")
    assert rows[0].status == "OPEN"
    assert db.query(LedgerEntry).count() == 0
    assert db.query(PaymentSettlement).count() == 0


def test_approved_agreement_webhook_uses_single_settlement_path(monkeypatch):
    from app.api import payments as payments_api

    db = _db()
    _, _, rows = _agreement(db, principal="100.00", penalty="10.00")
    payment = _payment(db, None, rows[0], amount="15.00", suffix="webhook")
    payment.provider_order_id = "order-webhook"
    db.commit()

    async def get_order(_self, _order_id):
        return {
            "status": "approved",
            "total_amount": "15.00",
            "transactions": {"payments": [{"id": payment.provider_payment_id, "status": "approved", "transaction_amount": "15.00"}]},
        }

    async def request_json():
        return {"id": "event-agreement-settlement", "type": "payment", "data": {"id": payment.provider_payment_id}}

    monkeypatch.setattr(payments_api, "validate_mercado_pago_signature", lambda *args, **kwargs: True)
    monkeypatch.setattr(payments_api.MercadoPagoClient, "get_order", get_order)
    request = SimpleNamespace(headers={}, query_params={"data.id": payment.provider_payment_id}, json=request_json)

    response = asyncio.run(payments_api.mercado_pago_webhook(request, db))

    assert response == {"received": True}
    assert db.query(PaymentSettlement).filter_by(payment_id=payment.id).count() == 1
    assert db.query(LedgerEntry).filter_by(reference_type="AGREEMENT_INSTALLMENT_PAYMENT", reference_id=str(payment.id)).count() == 1
    assert rows[0].paid_penalty_amount == Decimal("10.00")
    assert rows[0].paid_amount == Decimal("5.00")
