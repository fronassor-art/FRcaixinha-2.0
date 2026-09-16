from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
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
    PaymentSettlement,
    Payment,
    User,
)
from app.services.agreement_payments_v039 import apply_confirmed_agreement_payment
from app.services import agreement_payments_v039


def make_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def make_payment(db, *, amount="100.00", received=None, penalty="0.00"):
    group = Group(name="Grupo acordo")
    user = User(name="Membro", email="agreement@test", cpf="agreement-cpf", password_hash="x")
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
        amount=Decimal("100.00") + Decimal(penalty),
        penalty_amount=Decimal(penalty),
        status="OPEN",
    )
    db.add(installment)
    db.flush()
    payment = Payment(
        provider="mercado_pago",
        provider_payment_id=f"provider-{installment.id}",
        idempotency_key=f"idempotency-{installment.id}",
        amount=Decimal(amount),
        amount_received=None if received is None else Decimal(received),
        status="approved",
        raw_status="approved",
        reference_type="AGREEMENT_INSTALLMENT",
        reference_id=str(installment.id),
    )
    db.add(payment)
    db.flush()
    return payment, installment


def ledger_rows(db, payment):
    return db.query(LedgerEntry).filter(LedgerEntry.reference_id == str(payment.id)).all()


def test_amount_received_is_authoritative_for_partial_payment():
    db = make_db()
    payment, installment = make_payment(db, amount="100.00", received="40.00")

    assert apply_confirmed_agreement_payment(db, payment, installment) is True
    db.commit()

    assert installment.paid_amount == Decimal("40.00")
    assert installment.status == "PARTIAL"
    assert payment.ledger_posted_at is not None
    assert [entry.amount for entry in ledger_rows(db, payment)] == [Decimal("40.00")]
    db.close()


def test_full_payment_preserves_existing_behavior():
    db = make_db()
    payment, installment = make_payment(db, amount="100.00", received="100.00")

    assert apply_confirmed_agreement_payment(db, payment, installment) is True
    db.commit()

    assert installment.paid_amount == Decimal("100.00")
    assert installment.status == "PAID"
    assert ledger_rows(db, payment)[0].amount == Decimal("100.00")
    db.close()


def test_missing_zero_and_negative_received_do_not_change_financial_state():
    for received in (None, "0.00", "-1.00"):
        db = make_db()
        payment, installment = make_payment(db, amount="100.00", received=received)
        before = (installment.paid_amount, installment.paid_penalty_amount, installment.status)

        assert apply_confirmed_agreement_payment(db, payment, installment) is False
        db.commit()

        assert (installment.paid_amount, installment.paid_penalty_amount, installment.status) == before
        assert payment.ledger_posted_at is None
        assert ledger_rows(db, payment) == []
        db.close()


def test_positive_received_with_no_balance_does_not_mark_payment_posted():
    db = make_db()
    payment, installment = make_payment(db, amount="100.00", received="40.00")
    installment.paid_amount = Decimal("100.00")
    db.flush()

    assert apply_confirmed_agreement_payment(db, payment, installment) is False
    db.commit()

    assert payment.ledger_posted_at is None
    assert ledger_rows(db, payment) == []
    db.close()


def test_received_above_balance_is_capped_at_balance():
    db = make_db()
    payment, installment = make_payment(db, amount="150.00", received="150.00")

    assert apply_confirmed_agreement_payment(db, payment, installment) is True
    db.commit()

    assert installment.paid_amount == Decimal("100.00")
    assert ledger_rows(db, payment)[0].amount == Decimal("100.00")
    db.close()


def test_penalty_is_applied_before_principal_using_received_amount_only():
    db = make_db()
    payment, installment = make_payment(db, amount="100.00", received="15.00", penalty="10.00")

    assert apply_confirmed_agreement_payment(db, payment, installment) is True
    db.commit()

    assert installment.paid_penalty_amount == Decimal("10.00")
    assert installment.paid_amount == Decimal("5.00")
    assert installment.status == "PARTIAL"
    assert ledger_rows(db, payment)[0].amount == Decimal("15.00")
    db.close()


def test_repeated_payment_is_sequentially_idempotent():
    db = make_db()
    payment, installment = make_payment(db, amount="100.00", received="40.00")

    assert apply_confirmed_agreement_payment(db, payment, installment) is True
    db.commit()
    first_paid = installment.paid_amount
    first_ledger_count = len(ledger_rows(db, payment))

    assert apply_confirmed_agreement_payment(db, payment, installment) is False
    db.commit()

    assert installment.paid_amount == first_paid == Decimal("40.00")
    assert len(ledger_rows(db, payment)) == first_ledger_count == 1
    db.close()


def test_existing_ledger_with_unset_posted_at_does_not_reapply_installment():
    db = make_db()
    payment, installment = make_payment(db, amount="100.00", received="40.00", penalty="10.00")
    installment.paid_penalty_amount = Decimal("2.00")
    installment.paid_amount = Decimal("8.00")
    installment.status = "PARTIAL"
    existing = LedgerEntry(
        account="CAIXINHA",
        direction="CREDIT",
        amount=Decimal("40.00"),
        reference_type="AGREEMENT_INSTALLMENT_PAYMENT",
        reference_id=str(payment.id),
    )
    db.add(existing)
    db.flush()
    before = (installment.paid_amount, installment.paid_penalty_amount, installment.status)
    before_count = len(ledger_rows(db, payment))
    before_amount = existing.amount

    assert apply_confirmed_agreement_payment(db, payment, installment) is False
    db.commit()

    assert (installment.paid_amount, installment.paid_penalty_amount, installment.status) == before
    assert len(ledger_rows(db, payment)) == before_count == 1
    assert existing.amount == before_amount == Decimal("40.00")
    assert payment.ledger_posted_at is None

    assert apply_confirmed_agreement_payment(db, payment, installment) is False
    db.commit()
    assert (installment.paid_amount, installment.paid_penalty_amount, installment.status) == before
    assert len(ledger_rows(db, payment)) == 1
    db.close()


def test_legacy_facade_propagates_late_writer_error_and_rollback_restores_state(monkeypatch):
    db = make_db()
    payment, installment = make_payment(db, amount="100.00", received="40.00", penalty="10.00")
    agreement = db.get(CollectionAgreement, installment.agreement_id)
    db.commit()
    before = (installment.paid_amount, installment.paid_penalty_amount, installment.status, agreement.status, agreement.state_revision)

    def late_failure(session, row, **kwargs):
        installment.paid_amount = Decimal("40.00")
        installment.status = "PARTIAL"
        agreement.state_revision = 1
        session.add(LedgerEntry(account="CAIXINHA", direction="CREDIT", amount=Decimal("40.00"), reference_type="AGREEMENT_INSTALLMENT_PAYMENT", reference_id=str(row.id)))
        raise ValueError("falha tardia controlada")

    monkeypatch.setattr(agreement_payments_v039, "settle_confirmed_pix_payment", late_failure)
    with pytest.raises(ValueError, match="falha tardia"):
        apply_confirmed_agreement_payment(db, payment, installment)
    db.rollback()

    assert (installment.paid_amount, installment.paid_penalty_amount, installment.status, agreement.status, agreement.state_revision) == before
    assert db.query(PaymentSettlement).filter_by(payment_id=payment.id).count() == 0
    assert db.query(LedgerEntry).filter_by(reference_id=str(payment.id)).count() == 0
    db.close()


def test_legacy_facade_rejects_installment_mismatch_before_delegation(monkeypatch):
    db = make_db()
    payment, installment = make_payment(db, amount="100.00", received="40.00")
    other = AgreementInstallment(
        agreement_id=installment.agreement_id,
        number=2,
        due_date=date(2026, 2, 10),
        principal=Decimal("10.00"),
        amount=Decimal("10.00"),
        status="OPEN",
    )
    db.add(other)
    db.flush()
    called = False

    def unexpected(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(agreement_payments_v039, "settle_confirmed_pix_payment", unexpected)
    with pytest.raises(ValueError, match="diverge"):
        apply_confirmed_agreement_payment(db, payment, other)
    assert called is False
    assert installment.paid_amount == Decimal("0.00")
    assert db.query(PaymentSettlement).count() == 0
    assert db.query(LedgerEntry).count() == 0
    db.rollback()
    db.close()
