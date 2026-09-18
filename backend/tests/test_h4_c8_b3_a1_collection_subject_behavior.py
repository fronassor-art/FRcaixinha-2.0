from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import CollectionCase, CollectionEvent, Group, Loan, LoanInstallment, Member, User
from app.services.agreements_v039 import decide_agreement, request_agreement


def make_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed_open_loan_case(db):
    group = Group(name="H4-C8-B3-A1 group")
    user = User(
        name="H4-C8-B3-A1 member",
        email="h4-c8-b3-a1@example.com",
        cpf="h4-c8-b3-a1-cpf",
        password_hash="x",
    )
    db.add_all([group, user])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id)
    db.add(member)
    db.flush()
    loan = Loan(
        member_id=member.id,
        principal=Decimal("100.00"),
        monthly_rate=Decimal("0.20"),
        installments=1,
        status="OVERDUE",
    )
    db.add(loan)
    db.flush()
    installment = LoanInstallment(
        loan_id=loan.id,
        number=1,
        due_date=date.today() - timedelta(days=10),
        principal=Decimal("100.00"),
        interest=Decimal("0.00"),
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        paid_penalty_amount=Decimal("0.00"),
        penalty_amount=Decimal("0.00"),
        status="OVERDUE",
    )
    db.add(installment)
    db.flush()
    case = CollectionCase(
        member_id=member.id,
        loan_id=loan.id,
        status="OPEN",
        stage="INTENSIVE",
        opened_at=datetime.now(timezone.utc),
        next_action_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db.add(case)
    db.commit()
    return user, member, loan, installment, case


def test_agreement_approval_closes_superseded_open_loan_case():
    db = make_db()
    user, _member, loan, _installment, case = seed_open_loan_case(db)

    agreement = request_agreement(db, loan.id, user.id, installments=1)
    decide_agreement(db, agreement.id, user.id, approve=True)

    assert loan.status == "RESTRUCTURED"
    assert db.get(CollectionCase, case.id).status == "RESOLVED"


def test_agreement_case_contract_requires_agreement_subject_column():
    assert hasattr(CollectionCase, "agreement_id"), (
        "CollectionCase precisa de agreement_id antes de representar recovery de Agreement."
    )


def test_agreement_event_contract_requires_agreement_subject_column():
    assert hasattr(CollectionEvent, "agreement_installment_id"), (
        "CollectionEvent precisa de agreement_installment_id antes de gerar evento de Agreement."
    )


def test_agreement_event_notification_contract_requires_agreement_subject_column():
    assert hasattr(CollectionEvent, "agreement_installment_id"), (
        "Notification de Agreement depende de CollectionEvent com sujeito Agreement."
    )
