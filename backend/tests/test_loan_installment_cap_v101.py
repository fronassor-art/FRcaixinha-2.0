from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.admin_credit_policy import CreditPolicyIn
from app.db.base import Base
from app.models import Group, Loan, Member, User
from app.schemas.finance import LoanRequestIn
from app.services.approval_engine_v048 import assert_loan_approval_allowed


@pytest.mark.parametrize("installments", [7, 12, 24, 120])
def test_request_and_group_policy_reject_installments_above_six(installments):
    with pytest.raises(ValidationError):
        LoanRequestIn(principal=Decimal("100"), installments=installments, simulation_token="x" * 32)
    with pytest.raises(ValidationError):
        CreditPolicyIn(max_simultaneous_loans=1, max_installments=installments, grace_days=0, max_overdue_installments=0)


@pytest.mark.parametrize("installments", [1, 3, 6])
def test_request_accepts_installments_from_one_through_six(installments):
    request = LoanRequestIn(principal=Decimal("100"), installments=installments, simulation_token="x" * 32)
    assert request.installments == installments


def _session_with_member():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    group = Group(name="valid", max_installments=6)
    user = User(name="Admin", email="admin@cap.test", cpf="cap", password_hash="x")
    session.add_all([group, user]); session.flush()
    member = Member(user_id=user.id, group_id=group.id)
    session.add(member); session.flush()
    return session, user, member


def test_application_approval_exception_cannot_bypass_six_installments():
    session, user, member = _session_with_member()
    loan = Loan(member_id=member.id, principal=100, monthly_rate=0, installments=7)
    session.add(loan); session.flush()
    with pytest.raises(ValueError, match="LOAN_INSTALLMENTS_LIMIT_EXCEEDED"):
        assert_loan_approval_allowed(session, loan, user.id, force_exception=True, admin_note="Não excede teto.")
    session.close()


def test_existing_loan_with_six_installments_cannot_be_changed_to_seven_by_approval():
    session, user, member = _session_with_member()
    loan = Loan(member_id=member.id, principal=100, monthly_rate=0, installments=6)
    session.add(loan); session.commit()
    loan.installments = 7
    with pytest.raises(ValueError, match="LOAN_INSTALLMENTS_LIMIT_EXCEEDED"):
        assert_loan_approval_allowed(session, loan, user.id, force_exception=True, admin_note="Não excede teto.")
    session.close()


def test_historical_loan_with_twelve_installments_keeps_normal_updates_available():
    session, _, member = _session_with_member()
    loan = Loan(member_id=member.id, principal=100, monthly_rate=0, installments=12)
    session.add(loan); session.commit()
    now = datetime.now(timezone.utc)
    loan.status = "ACTIVE"
    loan.decided_at = now
    loan.disbursed_at = now
    loan.paid_at = now
    session.commit()
    assert session.get(Loan, loan.id).status == "ACTIVE"
    session.close()


def test_postgresql_trigger_migration_preserves_historical_loans_and_blocks_new_invalid_values():
    migration = Path("alembic/versions/0057_loan_installment_cap_v101.py").read_text()
    assert "BEFORE INSERT OR UPDATE OF installments ON loans" in migration
    assert "IS NOT DISTINCT FROM" in migration
    assert "OLD.installments > 6" in migration
    assert "CHECK (installments >= 1 AND installments <= 6)" not in migration


def test_migration_normalizes_only_group_installment_configuration():
    migration = Path("alembic/versions/0057_loan_installment_cap_v101.py").read_text()
    assert "WHEN max_installments > 6 THEN 6" in migration
    assert "WHEN max_installments < 1 THEN 1" in migration
    assert "UPDATE loans" not in migration
    assert "loan_installments" not in migration
    assert "agreement_installments" not in migration
