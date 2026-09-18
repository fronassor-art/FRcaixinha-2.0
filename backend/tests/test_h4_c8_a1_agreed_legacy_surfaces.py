from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.admin import dashboard, overdue_installments
from app.api.admin_finance import loan_engine_overdue
from app.db.base import Base
from app.models import Group, Loan, LoanInstallment, Member, Quota, User
from app.services.reconciliation_v032 import reconcile


def make_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed_installments(db):
    group = Group(name="H4-C8-A1 group")
    user = User(
        name="H4-C8-A1 member",
        email="h4-c8-a1@example.com",
        cpf="h4-c8-a1-cpf",
        password_hash="x",
    )
    db.add_all([group, user])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id)
    db.add(member)
    db.flush()
    db.add(Quota(member_id=member.id, units=1, status="ACTIVE"))
    loan = Loan(
        member_id=member.id,
        principal=Decimal("150.00"),
        monthly_rate=Decimal("0.20"),
        installments=2,
        status="RESTRUCTURED",
    )
    db.add(loan)
    db.flush()
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
    overdue = LoanInstallment(
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
    db.add_all([agreed, overdue])
    db.commit()
    return agreed, overdue


def test_admin_dashboard_excludes_agreed_from_outstanding_and_overdue():
    db = make_db()
    _agreed, overdue = seed_installments(db)

    result = dashboard(admin=object(), db=db)

    assert result["outstanding_loan_balance"] == "40.00"
    assert result["overdue_installments"] == 1
    assert overdue.id != _agreed.id


def test_admin_overdue_installments_excludes_agreed_but_keeps_overdue():
    db = make_db()
    agreed, overdue = seed_installments(db)

    result = overdue_installments(admin=object(), db=db)

    assert [item["id"] for item in result["items"]] == [overdue.id]
    assert agreed.id not in [item["id"] for item in result["items"]]


def test_admin_finance_loan_engine_overdue_excludes_agreed_but_keeps_overdue():
    db = make_db()
    agreed, overdue = seed_installments(db)

    result = loan_engine_overdue(admin=object(), db=db)

    assert [item["id"] for item in result["items"]] == [overdue.id]
    assert agreed.id not in [item["id"] for item in result["items"]]


def test_reconciliation_v032_does_not_treat_agreed_as_open_negative_balance():
    db = make_db()
    agreed, _overdue = seed_installments(db)
    agreed.paid_amount = Decimal("110.00")
    db.commit()

    result = reconcile(db)

    assert result["status"] == "PASS"
