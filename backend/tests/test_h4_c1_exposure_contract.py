from datetime import date
from decimal import Decimal

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
    LoanInstallment,
    Member,
    MemberFinancialAccount,
    MemberFinancialEntry,
    Quota,
    User,
)
from app.services.financial_obligations import agreement_installment_item
from app.services.reconciliation_v040 import build_advanced_reconciliation
from app.services.risk_v036 import exposure
from app.api.loans import _loan_eligibility


def make_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def make_agreement(
    db,
    *,
    status="APPROVED",
    installment_status="OPEN",
    principal="100.00",
    paid_amount="40.00",
    penalty_amount="10.00",
    paid_penalty_amount="3.00",
):
    group = Group(
        name="H4-C1 group",
        max_quota_multiple=Decimal("100.00"),
    )
    user = User(
        name="H4-C1 member",
        email="h4-c1@example.com",
        cpf="h4-c1-cpf",
        password_hash="x",
    )
    db.add_all([group, user])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id)
    db.add(member)
    db.flush()
    db.add(Quota(member_id=member.id, units=1, status="ACTIVE"))
    db.flush()
    loan = Loan(
        member_id=member.id,
        principal=Decimal("100.00"),
        monthly_rate=Decimal("0.20"),
        installments=1,
        status="RESTRUCTURED",
    )
    db.add(loan)
    db.flush()
    agreement = CollectionAgreement(
        loan_id=loan.id,
        member_id=member.id,
        requested_by=user.id,
        status=status,
        installments=1,
        total_amount=Decimal("110.00"),
        snapshot="{}",
    )
    db.add(agreement)
    db.flush()
    installment = AgreementInstallment(
        agreement_id=agreement.id,
        number=1,
        due_date=date(2026, 1, 1),
        principal=Decimal(principal),
        amount=Decimal("110.00"),
        penalty_amount=Decimal(penalty_amount),
        paid_amount=Decimal(paid_amount),
        paid_penalty_amount=Decimal(paid_penalty_amount),
        status=installment_status,
    )
    db.add(installment)
    db.flush()
    return group, user, member, loan, agreement, installment


def add_original_agreed_installment(db, loan):
    original = LoanInstallment(
        loan_id=loan.id,
        number=1,
        due_date=date(2025, 12, 1),
        principal=Decimal("100.00"),
        interest=Decimal("0.00"),
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        penalty_amount=Decimal("0.00"),
        paid_penalty_amount=Decimal("0.00"),
        status="AGREED",
    )
    db.add(original)
    db.flush()
    return original


def test_agreed_loan_installment_is_not_counted_with_replacement_agreement():
    db = make_db()
    _group, _user, _member, loan, _agreement, _installment = make_agreement(db)
    add_original_agreed_installment(db, loan)
    db.commit()

    snapshot = build_advanced_reconciliation(db, date(2026, 1, 1))["snapshot"]

    assert snapshot["open_loan_exposure"] == "0.00"
    assert snapshot["open_agreement_exposure"] == "67.00"


def test_agreement_components_remain_separate_and_total_is_not_double_counted():
    db = make_db()
    _group, _user, _member, _loan, agreement, installment = make_agreement(db)
    item = agreement_installment_item(db, installment, agreement)

    assert item["principal_outstanding"] == "60.00"
    assert item["penalty_outstanding"] == "7.00"
    assert item["interest_outstanding"] == "0.00"
    assert item["outstanding_amount"] == "67.00"


def test_risk_agreement_exposure_is_not_negative_when_components_are_overpaid():
    db = make_db()
    _group, _user, member, _loan, agreement, installment = make_agreement(db)
    installment.paid_amount = Decimal("110.00")
    installment.paid_penalty_amount = Decimal("12.00")
    db.commit()

    assert exposure(db, member.id) == Decimal("0.00")


def test_settled_agreement_has_no_open_exposure_and_restored_state_returns_it():
    db = make_db()
    _group, _user, _member, _loan, agreement, installment = make_agreement(
        db,
        status="SETTLED",
        installment_status="PAID",
    )
    db.commit()

    settled = build_advanced_reconciliation(db, date(2026, 1, 1))["snapshot"]
    assert settled["open_agreement_exposure"] == "0.00"

    agreement.status = "APPROVED"
    installment.status = "PARTIAL"
    db.commit()
    restored = build_advanced_reconciliation(db, date(2026, 1, 1))["snapshot"]
    assert restored["open_agreement_exposure"] == "67.00"


def test_approved_agreement_blocks_new_loan_eligibility():
    db = make_db()
    group, user, member, loan, _agreement, _installment = make_agreement(db)
    account = MemberFinancialAccount(member_id=member.id)
    db.add(account)
    db.flush()
    db.add(MemberFinancialEntry(
        account_id=account.id,
        entry_type="CONTRIBUTION",
        direction="CREDIT",
        amount=Decimal("200.00"),
    ))
    db.add(LedgerEntry(
        account="CAIXINHA",
        direction="CREDIT",
        amount=Decimal("1000.00"),
        reference_type="TEST",
        reference_id="h4-c1",
    ))
    candidate = Loan(
        member_id=member.id,
        principal=Decimal("50.00"),
        monthly_rate=Decimal("0.20"),
        installments=1,
        status="REQUESTED",
    )
    db.add(candidate)
    db.commit()

    result = _loan_eligibility(member, candidate, db)

    assert result.eligible is False


def test_settled_agreement_does_not_create_debt_by_itself():
    db = make_db()
    group, user, member, _loan, _agreement, _installment = make_agreement(
        db,
        status="SETTLED",
        installment_status="PAID",
    )
    account = MemberFinancialAccount(member_id=member.id)
    db.add(account)
    db.flush()
    db.add(MemberFinancialEntry(
        account_id=account.id,
        entry_type="CONTRIBUTION",
        direction="CREDIT",
        amount=Decimal("200.00"),
    ))
    db.add(LedgerEntry(
        account="CAIXINHA",
        direction="CREDIT",
        amount=Decimal("1000.00"),
        reference_type="TEST",
        reference_id="h4-c1-settled",
    ))
    candidate = Loan(
        member_id=member.id,
        principal=Decimal("50.00"),
        monthly_rate=Decimal("0.20"),
        installments=1,
        status="REQUESTED",
    )
    db.add(candidate)
    db.commit()

    result = _loan_eligibility(member, candidate, db)

    assert result.eligible is True


def eligibility_for_agreement(db, **agreement_kwargs):
    _group, _user, member, _loan, _agreement, _installment = make_agreement(
        db, **agreement_kwargs
    )
    account = MemberFinancialAccount(member_id=member.id)
    db.add(account)
    db.flush()
    db.add(MemberFinancialEntry(
        account_id=account.id,
        entry_type="CONTRIBUTION",
        direction="CREDIT",
        amount=Decimal("200.00"),
    ))
    db.add(LedgerEntry(
        account="CAIXINHA",
        direction="CREDIT",
        amount=Decimal("1000.00"),
        reference_type="TEST",
        reference_id="h4-c1-control",
    ))
    candidate = Loan(
        member_id=member.id,
        principal=Decimal("50.00"),
        monthly_rate=Decimal("0.20"),
        installments=1,
        status="REQUESTED",
    )
    db.add(candidate)
    db.commit()
    return _loan_eligibility(member, candidate, db)


def test_approved_fully_paid_agreement_does_not_block_new_loan():
    db = make_db()
    result = eligibility_for_agreement(
        db,
        installment_status="PAID",
        paid_amount="100.00",
        paid_penalty_amount="10.00",
    )
    assert result.eligible is True


def test_approved_overpaid_agreement_does_not_block_new_loan():
    db = make_db()
    result = eligibility_for_agreement(
        db,
        paid_amount="110.00",
        paid_penalty_amount="12.00",
    )
    assert result.eligible is True


def test_approved_agreement_with_only_penalty_open_blocks_new_loan():
    db = make_db()
    result = eligibility_for_agreement(
        db,
        paid_amount="100.00",
        paid_penalty_amount="3.00",
    )
    assert result.eligible is False


def test_approved_agreement_with_only_principal_open_blocks_new_loan():
    db = make_db()
    result = eligibility_for_agreement(
        db,
        paid_amount="40.00",
        paid_penalty_amount="10.00",
    )
    assert result.eligible is False


def test_requested_agreement_does_not_block_new_loan_by_status_alone():
    db = make_db()
    result = eligibility_for_agreement(db, status="REQUESTED")
    assert result.eligible is True


def test_rejected_agreement_does_not_block_new_loan_by_status_alone():
    db = make_db()
    result = eligibility_for_agreement(db, status="REJECTED")
    assert result.eligible is True
