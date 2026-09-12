from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import User, Group, Member, Quota, Loan, AuditLog
from app.services.approval_engine_v048 import (
    evaluate_loan_pipeline,
    assert_loan_approval_allowed,
)


def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed_special_loan():
    s = db()

    user = User(
        name="Especial",
        email="especial@test.com",
        cpf="999",
        password_hash="x",
    )
    s.add(user)
    s.flush()

    group = Group(
        name="G",
        max_simultaneous_loans=1,
        max_installments=12,
        max_overdue_installments=2,
        max_quota_multiple=Decimal("10.00"),
        max_loan_amount=Decimal("3000.00"),
        max_loan_income_multiple=Decimal("2.00"),
        max_installment_income_ratio=Decimal("0.35"),
        min_on_time_ratio=Decimal("0.80"),
    )
    s.add(group)
    s.flush()

    member = Member(
        user_id=user.id,
        group_id=group.id,
        declared_monthly_income=Decimal("10000.00"),
    )
    s.add(member)
    s.flush()

    quota = Quota(
        member_id=member.id,
        units=Decimal("1.0000"),
        status="ACTIVE",
    )
    s.add(quota)

    loan = Loan(
        member_id=member.id,
        principal=Decimal("2500.00"),
        monthly_rate=Decimal("0.20"),
        installments=6,
        status="REQUESTED",
    )
    s.add(loan)
    s.commit()

    return s, user, loan


def test_special_credit_requires_exception_but_exception_is_accepted():
    s, admin, loan = seed_special_loan()

    result = evaluate_loan_pipeline(
        s,
        loan,
        persist_risk=False,
        include_release=False,
    )

    assert result["decision"] == "REVIEW"
    assert result["financial_risk"]["status"] == "PASS"
    assert result["credit_policy"]["status"] == "BLOCKED"

    allowed = assert_loan_approval_allowed(
        s,
        loan,
        admin.id,
        force_exception=True,
        admin_note="Crédito especial para homologação.",
    )

    assert allowed["decision"] == "REVIEW"

    audit = (
        s.query(AuditLog)
        .filter(
            AuditLog.action == "FINANCIAL_APPROVAL_EXCEPTION",
            AuditLog.entity_type == "LOAN",
            AuditLog.entity_id == str(loan.id),
        )
        .one()
    )

    assert "Crédito especial" in audit.details

    s.rollback()
    s.close()


def test_exception_does_not_change_pipeline_decision_to_allow():
    s, admin, loan = seed_special_loan()

    allowed = assert_loan_approval_allowed(
        s,
        loan,
        admin.id,
        force_exception=True,
        admin_note="Crédito especial para homologação.",
    )

    assert allowed["decision"] == "REVIEW"

    # A exceção autoriza a decisão administrativa, mas o pipeline
    # continua classificando a operação como REVIEW.
    result = evaluate_loan_pipeline(
        s,
        loan,
        persist_risk=False,
        include_release=False,
    )

    assert result["decision"] == "REVIEW"

    s.rollback()
    s.close()


def test_exception_does_not_bypass_release_liquidity():
    s, admin, loan = seed_special_loan()

    allowed = assert_loan_approval_allowed(
        s,
        loan,
        admin.id,
        force_exception=True,
        admin_note="Crédito especial para homologação.",
    )

    assert allowed["decision"] == "REVIEW"

    from app.models import Group, Member
    from app.services.risk_v036 import evaluate_release

    member = s.get(Member, loan.member_id)
    group = s.get(Group, member.group_id)

    release = evaluate_release(s, loan, group)

    assert release["status"] == "BLOCKED"
    assert release["cash_balance"] == Decimal("0.00")

    s.rollback()
    s.close()
