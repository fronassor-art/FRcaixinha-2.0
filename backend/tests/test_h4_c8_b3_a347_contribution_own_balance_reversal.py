from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import (
    AuditLog,
    Contribution,
    Group,
    LedgerEntry,
    Member,
    MemberFinancialEntry,
    Payment,
    PaymentReversal,
    PaymentSettlement,
    User,
)
from app.services import payment_reversal as reversal_service
from app.services.member_financial import get_member_financial_position
from app.services.payment_reversal import reverse_payment
from app.services.payment_settlement import settle_confirmed_pix_payment


def _db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _setup(db, *, amount="150.00", received=None, suffix="case"):
    admin = User(
        name=f"Master {suffix}",
        email=f"master-{suffix}@test",
        cpf=f"master-{suffix}",
        password_hash="x",
        role="ADMIN",
        is_active=True,
        is_master=True,
    )
    member_user = User(
        name=f"Member {suffix}",
        email=f"member-{suffix}@test",
        cpf=f"member-{suffix}",
        password_hash="x",
    )
    group = Group(name=f"Group {suffix}", max_installments=6)
    db.add_all([admin, member_user, group])
    db.flush()
    member = Member(user_id=member_user.id, group_id=group.id)
    db.add(member)
    db.flush()
    contribution = Contribution(
        member_id=member.id,
        competence=date(2026, 1, 1),
        amount=Decimal(amount),
        due_date=date(2026, 12, 1),
        status="PENDING",
        paid_amount=Decimal("0.00"),
    )
    db.add(contribution)
    db.flush()
    payment = Payment(
        provider="mercado_pago",
        provider_payment_id=f"provider-{suffix}",
        idempotency_key=f"idempotency-{suffix}",
        amount=Decimal(amount) if received is None else Decimal(received),
        amount_received=None if received is None else Decimal(received),
        status="approved",
        raw_status="approved",
        reference_type="CONTRIBUTION",
        reference_id=str(contribution.id),
    )
    db.add(payment)
    db.flush()
    settlement = settle_confirmed_pix_payment(
        db,
        payment,
        confirmation_source="A347",
        confirmed_at=datetime(2026, 9, 16, 3, 50, tzinfo=timezone.utc),
    )
    db.commit()
    return admin, member, contribution, payment, settlement


def _reverse(db, admin, payment):
    reversal = reverse_payment(
        db,
        payment_id=payment.id,
        admin_id=admin.id,
        reason="A347 reversal",
        now=datetime(2026, 9, 17, 3, 50, tzinfo=timezone.utc),
    )
    db.commit()
    return reversal


def test_integral_reversal_preserves_original_and_debits_position():
    db = _db()
    admin, member, contribution, payment, _settlement = _setup(db, suffix="integral")
    before = get_member_financial_position(db, member)

    reversal = _reverse(db, admin, payment)
    entries = db.query(MemberFinancialEntry).filter_by(contribution_id=contribution.id).all()
    position = get_member_financial_position(db, member)

    assert before["own_balance"] == Decimal("150.00")
    assert position == {
        "own_balance": Decimal("0.00"),
        "committed_balance": before["committed_balance"],
        "available_balance": Decimal("0.00"),
    }
    assert len(entries) == 2
    assert [(entry.entry_type, entry.direction, entry.amount) for entry in entries] == [
        ("CONTRIBUTION", "CREDIT", Decimal("150.00")),
        ("CONTRIBUTION_REVERSAL", "DEBIT", Decimal("150.00")),
    ]
    assert entries[0].id != entries[1].id
    assert entries[1].payment_reversal_id == reversal.id
    assert entries[1].contribution_id == contribution.id
    assert contribution.status == "PENDING"
    assert db.query(LedgerEntry).count() == 2
    assert db.query(AuditLog).filter_by(action="PAYMENT_REVERSED").count() == 1


def test_partial_payment_reversal_without_paid_state_creates_no_patrimonial_debit():
    db = _db()
    admin, member, contribution, payment, _settlement = _setup(db, received="40.00", suffix="partial")
    reversal = _reverse(db, admin, payment)

    assert contribution.status == "PENDING"
    assert get_member_financial_position(db, member)["own_balance"] == Decimal("0.00")
    assert db.query(MemberFinancialEntry).filter_by(contribution_id=contribution.id).count() == 0
    assert reversal.id is not None


def test_reversing_earlier_partial_payment_removes_active_full_credit():
    db = _db()
    admin, member, contribution, first_payment, _settlement = _setup(db, received="50.00", suffix="earlier")
    second_payment = Payment(
        provider="mercado_pago",
        provider_payment_id="provider-earlier-second",
        idempotency_key="idempotency-earlier-second",
        amount=Decimal("100.00"),
        amount_received=Decimal("100.00"),
        status="approved",
        raw_status="approved",
        reference_type="CONTRIBUTION",
        reference_id=str(contribution.id),
    )
    db.add(second_payment)
    db.flush()
    settle_confirmed_pix_payment(
        db,
        second_payment,
        confirmation_source="A347",
        confirmed_at=datetime(2026, 9, 16, 4, 50, tzinfo=timezone.utc),
    )
    db.commit()
    assert contribution.status == "PAID"
    assert db.query(MemberFinancialEntry).filter_by(contribution_id=contribution.id).count() == 1

    _reverse(db, admin, first_payment)

    assert contribution.status == "PARTIAL"
    assert contribution.paid_amount == Decimal("100.00")
    assert get_member_financial_position(db, member)["own_balance"] == Decimal("0.00")
    assert db.query(MemberFinancialEntry).filter_by(contribution_id=contribution.id).count() == 2


def test_reversal_retry_returns_same_reversal_without_second_mfe():
    db = _db()
    admin, member, contribution, payment, _settlement = _setup(db, suffix="retry")
    first = _reverse(db, admin, payment)
    second = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Outro motivo")
    db.commit()

    assert second.id == first.id
    assert db.query(MemberFinancialEntry).filter_by(contribution_id=contribution.id).count() == 2
    assert db.query(PaymentReversal).filter_by(payment_id=payment.id).count() == 1
    assert get_member_financial_position(db, member)["own_balance"] == Decimal("0.00")


def test_reversal_failure_after_compensating_mfe_rolls_back_everything(monkeypatch):
    db = _db()
    admin, member, contribution, payment, _settlement = _setup(db, suffix="rollback")
    monkeypatch.setattr(
        reversal_service,
        "_receipt_snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("receipt failure")),
    )

    with pytest.raises(RuntimeError, match="receipt failure"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Rollback A347")
    db.rollback()

    assert db.query(PaymentReversal).count() == 0
    assert db.query(MemberFinancialEntry).filter_by(contribution_id=contribution.id).count() == 1
    assert db.query(LedgerEntry).filter_by(reference_type="REVERSAL").count() == 0
    assert contribution.status == "PAID"
    assert get_member_financial_position(db, member)["own_balance"] == Decimal("150.00")


def test_reversal_enters_member_mutex_before_compensating_entry(monkeypatch):
    db = _db()
    admin, member, contribution, payment, _settlement = _setup(db, suffix="mutex")
    lock_entered = False
    write_after_lock = False
    original_lock = reversal_service.lock_member_financial_account
    original_add = reversal_service.add_member_financial_entry

    def observed_lock(session, member_or_id):
        nonlocal lock_entered
        result = original_lock(session, member_or_id)
        lock_entered = result[0].id == member.id
        return result

    def observed_add(*args, **kwargs):
        nonlocal write_after_lock
        assert lock_entered
        write_after_lock = True
        return original_add(*args, **kwargs)

    monkeypatch.setattr(reversal_service, "lock_member_financial_account", observed_lock)
    monkeypatch.setattr(reversal_service, "add_member_financial_entry", observed_add)
    _reverse(db, admin, payment)

    assert lock_entered is True
    assert write_after_lock is True
    assert contribution.status == "PENDING"


def test_repayment_after_reversal_creates_new_active_credit_episode():
    db = _db()
    admin, member, contribution, payment, _settlement = _setup(db, suffix="repay")
    _reverse(db, admin, payment)
    new_payment = Payment(
        provider="mercado_pago",
        provider_payment_id="provider-repay-second",
        idempotency_key="idempotency-repay-second",
        amount=Decimal("150.00"),
        amount_received=Decimal("150.00"),
        status="approved",
        raw_status="approved",
        reference_type="CONTRIBUTION",
        reference_id=str(contribution.id),
    )
    db.add(new_payment)
    db.flush()
    settle_confirmed_pix_payment(
        db,
        new_payment,
        confirmation_source="A347",
        confirmed_at=datetime(2026, 9, 18, 3, 50, tzinfo=timezone.utc),
    )
    db.commit()

    assert contribution.status == "PAID"
    assert get_member_financial_position(db, member)["own_balance"] == Decimal("150.00")
    assert db.query(MemberFinancialEntry).filter_by(contribution_id=contribution.id).count() == 3
