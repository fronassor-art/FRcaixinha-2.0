from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import AgreementInstallment, CollectionAgreement, Group, Loan, Member, User
from app.services.reconciliation_v040 import build_advanced_reconciliation


def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def agreement(db, suffix, rows):
    group = Group(name=f"Group {suffix}")
    user = User(name=f"Member {suffix}", email=f"{suffix}@test", cpf=f"cpf-{suffix}", password_hash="x")
    db.add_all([group, user]); db.flush()
    member = Member(user_id=user.id, group_id=group.id); db.add(member); db.flush()
    loan = Loan(member_id=member.id, principal=Decimal("100"), monthly_rate=Decimal("0.20"), installments=1, status="RESTRUCTURED")
    db.add(loan); db.flush()
    collection = CollectionAgreement(loan_id=loan.id, member_id=member.id, requested_by=user.id, status="APPROVED", installments=len(rows), total_amount=sum((Decimal(str(r["principal"])) + Decimal(str(r.get("penalty", "0"))) for r in rows), Decimal("0")), snapshot="{}")
    db.add(collection); db.flush()
    for number, row in enumerate(rows, 1):
        principal = Decimal(str(row["principal"])); penalty = Decimal(str(row.get("penalty", "0")))
        db.add(AgreementInstallment(agreement_id=collection.id, number=number, due_date=date(2026, 1, number), principal=principal, amount=principal + penalty, penalty_amount=penalty, paid_amount=Decimal(str(row.get("paid", "0"))), paid_penalty_amount=Decimal(str(row.get("paid_penalty", "0"))), status=row.get("status", "OPEN")))
    db.flush()
    return collection


def exposure(db):
    return build_advanced_reconciliation(db, date(2026, 1, 1))["snapshot"]["open_agreement_exposure"]


def test_agreement_exposure_without_penalty_or_payment():
    s = db(); agreement(s, "no-penalty", [{"principal": "100", "penalty": "0"}]); s.commit()
    assert exposure(s) == "100.00"


def test_agreement_exposure_with_principal_and_penalty_unpaid():
    s = db(); agreement(s, "unpaid", [{"principal": "100", "penalty": "10"}]); s.commit()
    assert exposure(s) == "110.00"


def test_agreement_exposure_with_penalty_partially_paid():
    s = db(); agreement(s, "penalty-partial", [{"principal": "100", "penalty": "10", "paid_penalty": "4"}]); s.commit()
    assert exposure(s) == "106.00"


def test_agreement_exposure_with_penalty_paid_and_principal_open():
    s = db(); agreement(s, "penalty-paid", [{"principal": "100", "penalty": "10", "paid_penalty": "10"}]); s.commit()
    assert exposure(s) == "100.00"


def test_agreement_exposure_with_principal_partially_paid():
    s = db(); agreement(s, "principal-partial", [{"principal": "100", "penalty": "10", "paid": "40"}]); s.commit()
    assert exposure(s) == "70.00"


def test_agreement_exposure_with_principal_and_penalty_partially_paid():
    s = db(); agreement(s, "both-partial", [{"principal": "100", "penalty": "10", "paid": "40", "paid_penalty": "4"}]); s.commit()
    assert exposure(s) == "66.00"


def test_agreement_exposure_for_fully_paid_installment_is_zero():
    s = db(); agreement(s, "paid", [{"principal": "100", "penalty": "10", "paid": "100", "paid_penalty": "10", "status": "PAID"}]); s.commit()
    assert exposure(s) == "0.00"


def test_agreement_exposure_when_paid_equals_due_is_zero():
    s = db(); agreement(s, "equal", [{"principal": "100", "penalty": "10", "paid": "100", "paid_penalty": "10"}]); s.commit()
    assert exposure(s) == "0.00"


def test_agreement_exposure_is_never_negative_for_overpaid_components():
    s = db(); agreement(s, "overpaid", [{"principal": "100", "penalty": "10", "paid": "101", "paid_penalty": "11"}]); s.commit()
    assert exposure(s) == "0.00"


def test_agreement_exposure_aggregates_multiple_installments_in_one_agreement():
    s = db(); agreement(s, "multi-installment", [{"principal": "100", "penalty": "10", "paid": "40", "paid_penalty": "10"}, {"principal": "80", "penalty": "0", "paid": "20"}]); s.commit()
    assert exposure(s) == "120.00"


def test_agreement_exposure_aggregates_multiple_agreements():
    s = db(); agreement(s, "agreement-one", [{"principal": "100", "penalty": "10", "paid": "50", "paid_penalty": "5"}]); agreement(s, "agreement-two", [{"principal": "80", "penalty": "4", "paid": "20", "paid_penalty": "4"}]); s.commit()
    assert exposure(s) == "115.00"
