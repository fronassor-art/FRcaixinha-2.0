from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import loans
from app.db.base import Base
from app.models import Group, Loan, LoanSimulation, Member, User
from app.schemas.finance import LoanRequestIn, LoanSimulationConfirmationIn, LoanSimulationIn


def _db_with_member(email="member@test.local"):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    group = Group(name=email, max_installments=6)
    user = User(name="Member", email=email, cpf=email, password_hash="x")
    db.add_all([group, user])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id)
    db.add(member)
    db.commit()
    return db, user, member


def _eligible():
    return SimpleNamespace(
        eligible=True, decision="APROVADO", reason="OK",
        own_balance=Decimal("100.00"), committed_balance=Decimal("0.00"),
        available_balance=Decimal("100.00"), outstanding_principal=Decimal("0.00"),
        normal_credit_limit=Decimal("100.00"), requested_amount=Decimal("100.00"),
        liquidity_available=Decimal("1000.00"),
    )


def test_simulation_is_official_sac_schedule_with_auditable_snapshot():
    db, user, member = _db_with_member()
    result = loans.simulate_loan(LoanSimulationIn(principal=Decimal("100"), installments=6), user, db)

    assert result["principal"] == "100.00"
    assert result["monthly_rate"] == "0.20"
    assert result["calculation_version"] == "linear_amortization_v1"
    assert result["installments_schedule"][0] == {
        "number": 1, "principal": "16.67", "interest": "20.00", "balance_after": "83.33",
        "amount": "36.67", "balance_before": "100.00",
    }
    assert result["installments_schedule"][-1]["balance_after"] == "0.00"
    assert result["totals"] == {"principal": "100.00", "interest": "69.99", "payment": "169.99", "final_balance": "0.00"}

    row = db.query(LoanSimulation).one()
    assert row.member_id == member.id
    assert row.monthly_rate == Decimal("0.20")
    assert row.calculation_version == "linear_amortization_v1"
    assert row.token_hash != result["simulation_token"]
    assert row.schedule_hash in row.schedule_json or len(row.schedule_hash) == 64
    db.close()


def test_request_requires_confirmation_and_consumes_matching_receipt(monkeypatch):
    db, user, _ = _db_with_member()
    simulation = loans.simulate_loan(LoanSimulationIn(principal=Decimal("100"), installments=3), user, db)
    request = LoanRequestIn(principal=Decimal("100"), installments=3, simulation_token=simulation["simulation_token"])

    with pytest.raises(HTTPException, match="NOT_CONFIRMED"):
        loans.request_loan(request, user, db)

    loans.confirm_simulation(LoanSimulationConfirmationIn(simulation_token=simulation["simulation_token"]), user, db)
    monkeypatch.setattr(loans, "_loan_eligibility", lambda *args: _eligible())
    result = loans.request_loan(request, user, db)

    row = db.query(LoanSimulation).one()
    loan = db.get(Loan, result["id"])
    assert row.status == "CONSUMED"
    assert row.loan_id == loan.id
    assert loan.monthly_rate == Decimal("0.20")
    with pytest.raises(HTTPException, match="ALREADY_CONSUMED"):
        loans.request_loan(request, user, db)
    db.close()


def test_receipt_rejects_incompatible_terms_and_wrong_member():
    db, user, _ = _db_with_member()
    simulation = loans.simulate_loan(LoanSimulationIn(principal=Decimal("100"), installments=3), user, db)
    loans.confirm_simulation(LoanSimulationConfirmationIn(simulation_token=simulation["simulation_token"]), user, db)

    with pytest.raises(HTTPException, match="TERMS_MISMATCH"):
        loans.request_loan(
            LoanRequestIn(principal=Decimal("101"), installments=3, simulation_token=simulation["simulation_token"]),
            user, db,
        )

    other_user = User(name="Other", email="other@test.local", cpf="other", password_hash="x")
    group = db.query(Group).first()
    db.add(other_user)
    db.flush()
    db.add(Member(user_id=other_user.id, group_id=group.id))
    db.commit()
    with pytest.raises(HTTPException) as error:
        loans.confirm_simulation(LoanSimulationConfirmationIn(simulation_token=simulation["simulation_token"]), other_user, db)
    assert error.value.status_code == 404
    db.close()


def test_expired_simulation_and_client_rate_are_rejected():
    db, user, _ = _db_with_member()
    simulation = loans.simulate_loan(LoanSimulationIn(principal=Decimal("100"), installments=1), user, db)
    row = db.query(LoanSimulation).one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    with pytest.raises(HTTPException, match="EXPIRED"):
        loans.confirm_simulation(LoanSimulationConfirmationIn(simulation_token=simulation["simulation_token"]), user, db)
    with pytest.raises(ValidationError):
        LoanRequestIn(principal=Decimal("100"), installments=1, simulation_token="x" * 32, monthly_rate=Decimal("0.10"))
    db.close()


def test_migration_is_additive_and_records_calculation_version():
    migration = Path("alembic/versions/0078_loan_simulation_confirmation_v102.py").read_text()
    assert "loan_simulations" in migration
    assert "calculation_version" in migration
    assert migration.count("op.create_table(") == 1
    assert 'down_revision = "0057_loan_installment_cap_v101"' in migration
    assert "UPDATE " not in migration
    assert "DELETE " not in migration
    assert "op.alter_column(" not in migration
