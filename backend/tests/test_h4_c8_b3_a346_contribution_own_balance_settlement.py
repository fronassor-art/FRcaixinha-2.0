from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import (
    Contribution,
    Group,
    LedgerEntry,
    Member,
    MemberFinancialEntry,
    Payment,
    PaymentSettlement,
    User,
)
from app.services import payment_settlement as payment_settlement_service
from app.services.member_financial import get_member_financial_position
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


def _member(db, suffix="member"):
    user = User(
        name=f"Member {suffix}",
        email=f"member-{suffix}@test",
        cpf=f"member-{suffix}",
        password_hash="x",
    )
    group = Group(name=f"Group {suffix}", max_installments=6)
    db.add_all([user, group])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id)
    db.add(member)
    db.flush()
    return member


def _contribution(db, member, *, amount="150.00", suffix="contribution"):
    contribution = Contribution(
        member_id=member.id,
        competence=date(2026, 1, 1),
        amount=Decimal(amount),
        due_date=date(2026, 12, 1),
        status="PENDING",
    )
    db.add(contribution)
    db.flush()
    payment = Payment(
        provider="mercado_pago",
        provider_payment_id=f"provider-{suffix}",
        idempotency_key=f"idempotency-{suffix}",
        amount=Decimal(amount),
        status="approved",
        raw_status="approved",
        reference_type="CONTRIBUTION",
        reference_id=str(contribution.id),
    )
    db.add(payment)
    db.flush()
    return contribution, payment


def _settle(db, payment):
    settlement = settle_confirmed_pix_payment(
        db,
        payment,
        confirmation_source="WEBHOOK",
        confirmed_at=datetime(2026, 9, 16, 3, 50, tzinfo=timezone.utc),
    )
    db.commit()
    return settlement


def test_full_confirmed_contribution_creates_one_credit_and_updates_position():
    db = _db()
    member = _member(db, "full")
    contribution, payment = _contribution(db, member, suffix="full")
    before = get_member_financial_position(db, member)

    settlement = _settle(db, payment)
    entries = db.query(MemberFinancialEntry).all()

    assert contribution.status == "PAID"
    assert contribution.paid_amount == Decimal("150.00")
    assert len(entries) == 1
    assert entries[0].entry_type == "CONTRIBUTION"
    assert entries[0].direction == "CREDIT"
    assert entries[0].amount == Decimal("150.00")
    assert entries[0].contribution_id == contribution.id
    assert entries[0].payment_settlement_id == settlement.id
    assert get_member_financial_position(db, member) == {
        "own_balance": before["own_balance"] + Decimal("150.00"),
        "committed_balance": before["committed_balance"],
        "available_balance": before["available_balance"] + Decimal("150.00"),
    }
    assert db.query(LedgerEntry).count() == 1
    assert db.query(PaymentSettlement).count() == 1


def test_partial_then_completion_creates_only_full_contribution_credit():
    db = _db()
    member = _member(db, "partial")
    contribution, first = _contribution(db, member, suffix="partial-1")
    first.amount = Decimal("100.00")
    first.amount_received = Decimal("100.00")
    db.flush()

    _settle(db, first)
    assert contribution.status == "PARTIAL"
    assert db.query(MemberFinancialEntry).count() == 0

    second = Payment(
        provider="mercado_pago",
        provider_payment_id="provider-partial-2",
        idempotency_key="idempotency-partial-2",
        amount=Decimal("50.00"),
        amount_received=Decimal("50.00"),
        status="approved",
        raw_status="approved",
        reference_type="CONTRIBUTION",
        reference_id=str(contribution.id),
    )
    db.add(second)
    db.flush()
    _settle(db, second)

    assert contribution.status == "PAID"
    assert contribution.paid_amount == Decimal("150.00")
    entries = db.query(MemberFinancialEntry).all()
    assert len(entries) == 1
    assert entries[0].amount == Decimal("150.00")
    assert entries[0].payment_settlement_id == db.query(PaymentSettlement).filter_by(payment_id=second.id).one().id


def test_committed_balance_is_unchanged_and_available_balance_uses_existing_formula():
    db = _db()
    member = _member(db, "committed")
    from app.services.member_financial import add_member_financial_entry

    add_member_financial_entry(
        db,
        member,
        entry_type="LOAN_PRINCIPAL_COMMITMENT",
        direction="CREDIT",
        amount=Decimal("25.00"),
    )
    db.commit()
    contribution, payment = _contribution(db, member, suffix="committed")
    before = get_member_financial_position(db, member)
    _settle(db, payment)
    after = get_member_financial_position(db, member)

    assert before == {"own_balance": Decimal("0.00"), "committed_balance": Decimal("25.00"), "available_balance": Decimal("0.00")}
    assert after == {"own_balance": Decimal("150.00"), "committed_balance": Decimal("25.00"), "available_balance": Decimal("125.00")}


def test_reprocessing_same_payment_does_not_create_second_credit():
    db = _db()
    member = _member(db, "retry")
    contribution, payment = _contribution(db, member, suffix="retry")
    first = _settle(db, payment)
    second = settle_confirmed_pix_payment(
        db,
        payment,
        confirmation_source="WEBHOOK",
        confirmed_at=datetime(2026, 9, 16, 3, 50, tzinfo=timezone.utc),
    )
    db.commit()

    assert second.id == first.id
    assert db.query(MemberFinancialEntry).filter_by(contribution_id=contribution.id).count() == 1
    assert db.query(PaymentSettlement).count() == 1


def test_contribution_settlement_uses_member_mutex_before_patrimonial_write(monkeypatch):
    db = _db()
    member = _member(db, "mutex")
    contribution, payment = _contribution(db, member, suffix="mutex")
    calls = []
    original = payment_settlement_service.lock_member_financial_account

    def observing_lock(session, member_or_id):
        calls.append(member_or_id.id if isinstance(member_or_id, Member) else member_or_id)
        return original(session, member_or_id)

    monkeypatch.setattr(payment_settlement_service, "lock_member_financial_account", observing_lock)
    settlement = _settle(db, payment)

    assert calls == [member.id]
    entry = db.query(MemberFinancialEntry).one()
    assert entry.payment_settlement_id == settlement.id
    assert entry.contribution_id == contribution.id


def test_failure_after_mfe_flush_rolls_back_all_settlement_effects(monkeypatch):
    db = _db()
    member = _member(db, "rollback")
    contribution, payment = _contribution(db, member, suffix="rollback")
    db.commit()
    monkeypatch.setattr(
        payment_settlement_service,
        "_receipt_snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("receipt failure")),
    )

    with pytest.raises(RuntimeError, match="receipt failure"):
        settle_confirmed_pix_payment(
            db,
            payment,
            confirmation_source="WEBHOOK",
            confirmed_at=datetime(2026, 9, 16, 3, 50, tzinfo=timezone.utc),
        )
    db.rollback()

    assert db.query(MemberFinancialEntry).count() == 0
    assert db.query(LedgerEntry).count() == 0
    assert db.query(PaymentSettlement).count() == 0
    restored = db.get(Contribution, contribution.id)
    assert restored.status == "PENDING"
    assert restored.paid_amount in (None, Decimal("0.00"))
