"""Permanent regression for the Member financial mutex protocol.

The original A3.40 diagnostic proved that Contribution settlement bypassed the
mutex used by Loan. The approved financial rule now makes a confirmed
Contribution patrimonial, so this regression proves the positive protocol:
both same-Member writers enter Member -> MemberFinancialAccount, and the
Contribution lock is acquired before its MFE is created.

SQLite only exercises protocol participation. It is not evidence of
PostgreSQL row-lock serialization.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from threading import Event

from sqlalchemy import create_engine, event as sqlalchemy_event
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import (
    Contribution,
    Group,
    Loan,
    LoanInstallment,
    Member,
    MemberFinancialAccount,
    MemberFinancialEntry,
    Payment,
    PaymentSettlement,
    User,
)
from app.services import loan_payments_v17, payment_settlement
from app.services.loan_payments_v17 import settle_loan_with_own_balance
from app.services.member_financial import lock_member_financial_account as real_member_lock
from app.services.payment_settlement import settle_confirmed_pix_payment


def _sqlite_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'a340_member_mutex.db'}",
        connect_args={"check_same_thread": False},
    )

    @sqlalchemy_event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _seed(Session):
    db = Session()
    user = User(name="A340 Member", email="a340-member@example.com", cpf="34000000001", password_hash="test")
    group = Group(name="A340 Group")
    db.add_all([user, group])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id)
    db.add(member)
    db.flush()

    loan = Loan(
        member_id=member.id,
        principal=Decimal("100.00"),
        monthly_rate=Decimal("0.20"),
        installments=1,
        status="ACTIVE",
    )
    db.add(loan)
    db.flush()
    db.add(LoanInstallment(
        loan_id=loan.id,
        number=1,
        due_date=date(2030, 1, 1),
        principal=Decimal("100.00"),
        interest=Decimal("0.00"),
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        paid_penalty_amount=Decimal("0.00"),
        status="OPEN",
    ))
    account = MemberFinancialAccount(member_id=member.id)
    db.add(account)
    db.flush()
    db.add(MemberFinancialEntry(
        account_id=account.id,
        entry_type="CONTRIBUTION",
        direction="CREDIT",
        amount=Decimal("100.00"),
        reference_type="A340_FIXTURE",
        reference_id="seed",
        description="Saldo próprio determinístico da regressão A3.40.",
    ))

    contribution = Contribution(
        member_id=member.id,
        competence=date(2030, 2, 1),
        amount=Decimal("10.00"),
        status="PENDING",
        paid_amount=Decimal("0.00"),
    )
    db.add(contribution)
    db.flush()
    payment = Payment(
        provider="a340",
        provider_payment_id="a340-contribution-payment",
        idempotency_key="a340-contribution-idempotency",
        amount=Decimal("10.00"),
        amount_received=Decimal("10.00"),
        status="approved",
        reference_type="CONTRIBUTION",
        reference_id=str(contribution.id),
    )
    db.add(payment)
    db.commit()
    result = {
        "member_id": member.id,
        "loan_id": loan.id,
        "contribution_id": contribution.id,
        "payment_id": payment.id,
    }
    db.close()
    return result


def test_same_member_loan_and_contribution_share_positive_mutex_protocol(tmp_path, monkeypatch):
    engine, Session = _sqlite_session_factory(tmp_path)
    ids = _seed(Session)
    loan_session = Session()
    contribution_session = Session()
    loan_mutex_entered = Event()
    contribution_mutex_entered = Event()
    patrimonial_write_after_mutex = Event()
    calls = []

    original_loan_lock = loan_payments_v17.lock_member_financial_account
    original_contribution_lock = payment_settlement.lock_member_financial_account
    original_add_entry = payment_settlement.add_member_financial_entry

    def observe_loan_lock(db, member_or_id):
        locked = original_loan_lock(db, member_or_id)
        calls.append(("loan", locked[0].id))
        loan_mutex_entered.set()
        return locked

    def observe_contribution_lock(db, member_or_id):
        locked = original_contribution_lock(db, member_or_id)
        calls.append(("contribution", locked[0].id))
        contribution_mutex_entered.set()
        return locked

    def observe_patrimonial_write(*args, **kwargs):
        assert contribution_mutex_entered.is_set()
        patrimonial_write_after_mutex.set()
        return original_add_entry(*args, **kwargs)

    monkeypatch.setattr(loan_payments_v17, "lock_member_financial_account", observe_loan_lock)
    monkeypatch.setattr(payment_settlement, "lock_member_financial_account", observe_contribution_lock)
    monkeypatch.setattr(payment_settlement, "add_member_financial_entry", observe_patrimonial_write)

    try:
        loan = loan_session.get(Loan, ids["loan_id"])
        loan_result = settle_loan_with_own_balance(loan_session, loan, actor_id=1)
        loan_session.commit()

        payment = contribution_session.get(Payment, ids["payment_id"])
        settlement = settle_confirmed_pix_payment(
            contribution_session,
            payment,
            confirmation_source="A340R1",
            confirmed_at=datetime(2026, 9, 16, 3, 50, tzinfo=timezone.utc),
        )
        contribution_session.commit()

        assert loan_result["settled"] is True
        assert settlement.obligation_type == "CONTRIBUTION"
        assert loan_mutex_entered.is_set()
        assert contribution_mutex_entered.is_set()
        assert patrimonial_write_after_mutex.is_set()
        assert calls == [("loan", ids["member_id"]), ("contribution", ids["member_id"])]

        check = Session()
        try:
            contribution = check.get(Contribution, ids["contribution_id"])
            entries = check.query(MemberFinancialEntry).join(MemberFinancialAccount).filter(
                MemberFinancialAccount.member_id == ids["member_id"]
            ).all()
            settlements = check.query(PaymentSettlement).filter_by(payment_id=ids["payment_id"]).count()
            assert contribution.status == "PAID"
            assert settlements == 1
            assert sum(entry.entry_type == "CONTRIBUTION" and entry.direction == "CREDIT" for entry in entries) == 2
        finally:
            check.close()
    finally:
        loan_session.close()
        contribution_session.close()
        engine.dispose()
