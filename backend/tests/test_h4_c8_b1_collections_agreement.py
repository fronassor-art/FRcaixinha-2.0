from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import (
    AgreementInstallment,
    CollectionAgreement,
    CollectionCase,
    CollectionEvent,
    Group,
    Loan,
    LoanInstallment,
    Member,
    User,
)
from app.services.collection_recovery_v049 import sync_cases
from app.services.collections_v038 import collections_summary, run_collection_cycle


def make_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed_member_loan(db):
    group = Group(name="H4-C8-B1 group")
    user = User(
        name="H4-C8-B1 member",
        email="h4-c8-b1@example.com",
        cpf="h4-c8-b1-cpf",
        password_hash="x",
    )
    db.add_all([group, user])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id)
    db.add(member)
    db.flush()
    loan = Loan(
        member_id=member.id,
        principal=Decimal("150.00"),
        monthly_rate=Decimal("0.20"),
        installments=2,
        status="RESTRUCTURED",
    )
    db.add(loan)
    db.flush()
    return group, user, member, loan


def add_loan_installments(db, loan, *, include_active=True):
    due_date = date.today() - timedelta(days=10)
    agreed = LoanInstallment(
        loan_id=loan.id,
        number=1,
        due_date=due_date,
        principal=Decimal("100.00"),
        interest=Decimal("0.00"),
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        paid_penalty_amount=Decimal("0.00"),
        penalty_amount=Decimal("0.00"),
        status="AGREED",
    )
    db.add(agreed)
    active = None
    if include_active:
        active = LoanInstallment(
            loan_id=loan.id,
            number=2,
            due_date=due_date,
            principal=Decimal("50.00"),
            interest=Decimal("0.00"),
            amount=Decimal("50.00"),
            paid_amount=Decimal("10.00"),
            paid_penalty_amount=Decimal("0.00"),
            penalty_amount=Decimal("0.00"),
            status="OVERDUE",
        )
        db.add(active)
    db.flush()
    return agreed, active


def add_agreement(db, user, member, loan, *, principal="100.00", paid="40.00", penalty="10.00", paid_penalty="3.00"):
    agreement = CollectionAgreement(
        loan_id=loan.id,
        member_id=member.id,
        requested_by=user.id,
        decided_by=user.id,
        status="APPROVED",
        installments=1,
        total_amount=Decimal(principal) + Decimal(penalty),
        snapshot="{}",
    )
    db.add(agreement)
    db.flush()
    installment = AgreementInstallment(
        agreement_id=agreement.id,
        number=1,
        due_date=date.today() - timedelta(days=10),
        principal=Decimal(principal),
        paid_amount=Decimal(paid),
        penalty_amount=Decimal(penalty),
        paid_penalty_amount=Decimal(paid_penalty),
        amount=Decimal(principal) + Decimal(penalty),
        status="OPEN",
    )
    db.add(installment)
    db.commit()
    return agreement, installment


def test_collections_summary_excludes_agreed_and_keeps_active_loan_control():
    db = make_db()
    _group, _user, _member, loan = seed_member_loan(db)
    _agreed, active = add_loan_installments(db, loan)
    result = collections_summary(db)

    assert result["overdue_installments"] == 1
    assert result["overdue_balance"] == "40.00"
    assert active.status == "OVERDUE"


def test_collection_cycle_does_not_create_action_for_agreed_but_processes_active_loan():
    db = make_db()
    _group, _user, _member, loan = seed_member_loan(db)
    agreed, active = add_loan_installments(db, loan)

    result = run_collection_cycle(db)
    db.commit()

    assert result["installments_scanned"] == 1
    assert result["events_created"] == 1
    events = db.query(CollectionEvent).all()
    assert [event.installment_id for event in events] == [active.id]
    assert agreed.id not in [event.installment_id for event in events]


def test_collections_summary_counts_approved_agreement_once_without_loan_double_count():
    db = make_db()
    _group, user, member, loan = seed_member_loan(db)
    agreed, _active = add_loan_installments(db, loan, include_active=False)
    _agreement, agreement_installment = add_agreement(db, user, member, loan)

    result = collections_summary(db)

    assert agreed.status == "AGREED"
    assert result["overdue_installments"] == 1
    assert result["overdue_balance"] == "67.00"
    assert agreement_installment.due_date < date.today()


@pytest.mark.parametrize(
    ("principal", "paid", "penalty", "paid_penalty", "expected"),
    [
        ("100.00", "40.00", "0.00", "0.00", "60.00"),
        ("100.00", "100.00", "10.00", "3.00", "7.00"),
        ("100.00", "110.00", "10.00", "3.00", "7.00"),
        ("100.00", "40.00", "10.00", "12.00", "60.00"),
    ],
)
def test_collections_summary_agreement_balance_clamps_components(
    principal, paid, penalty, paid_penalty, expected
):
    db = make_db()
    _group, user, member, loan = seed_member_loan(db)
    _agreement, _installment = add_agreement(
        db,
        user,
        member,
        loan,
        principal=principal,
        paid=paid,
        penalty=penalty,
        paid_penalty=paid_penalty,
    )

    result = collections_summary(db)

    assert result["overdue_installments"] == 1
    assert result["overdue_balance"] == expected


def test_recovery_does_not_open_loan_case_for_agreed_installment():
    db = make_db()
    _group, _user, _member, loan = seed_member_loan(db)
    agreed, _active = add_loan_installments(db, loan, include_active=False)

    result = sync_cases(db)

    assert result["cases_opened"] == 0
    assert db.query(CollectionCase).count() == 0
    assert agreed.status == "AGREED"
