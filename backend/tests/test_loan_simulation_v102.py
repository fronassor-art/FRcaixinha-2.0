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


def test_simulation_is_official_price_schedule_with_auditable_snapshot():
    db, user, member = _db_with_member()
    result = loans.simulate_loan(LoanSimulationIn(principal=Decimal("150"), installments=6), user, db)

    assert result["principal"] == "150.00"
    assert result["monthly_rate"] == "0.20"
    assert result["calculation_version"] == "price_amortization_v1"
    assert result["installments_schedule"] == [
        {"number": 1, "principal": "15.11", "interest": "30.00", "balance_after": "134.89", "amount": "45.11", "balance_before": "150.00"},
        {"number": 2, "principal": "18.13", "interest": "26.98", "balance_after": "116.76", "amount": "45.11", "balance_before": "134.89"},
        {"number": 3, "principal": "21.76", "interest": "23.35", "balance_after": "95.00", "amount": "45.11", "balance_before": "116.76"},
        {"number": 4, "principal": "26.11", "interest": "19.00", "balance_after": "68.89", "amount": "45.11", "balance_before": "95.00"},
        {"number": 5, "principal": "31.33", "interest": "13.78", "balance_after": "37.56", "amount": "45.11", "balance_before": "68.89"},
        {"number": 6, "principal": "37.56", "interest": "7.51", "balance_after": "0.00", "amount": "45.07", "balance_before": "37.56"},
    ]
    assert result["installments_schedule"][-1]["balance_after"] == "0.00"
    assert result["totals"] == {"principal": "150.00", "interest": "120.62", "payment": "270.62", "final_balance": "0.00"}

    row = db.query(LoanSimulation).one()
    assert row.member_id == member.id
    assert row.monthly_rate == Decimal("0.20")
    assert row.calculation_version == "price_amortization_v1"
    assert row.token_hash != result["simulation_token"]
    assert row.schedule_hash in row.schedule_json or len(row.schedule_hash) == 64
    assert row.schedule_hash == __import__("hashlib").sha256(row.schedule_json.encode("utf-8")).hexdigest()
    db.close()


def test_simulation_hash_and_contract_are_preserved_by_confirmation():
    db, user, _ = _db_with_member()
    simulation = loans.simulate_loan(
        LoanSimulationIn(principal=Decimal("150"), installments=6), user, db
    )
    row = db.query(LoanSimulation).one()
    original_hash = row.schedule_hash
    loans.confirm_simulation(
        LoanSimulationConfirmationIn(simulation_token=simulation["simulation_token"]),
        user,
        db,
    )
    db.refresh(row)
    assert row.status == "CONFIRMED"
    assert row.calculation_version == "price_amortization_v1"
    assert row.schedule_hash == original_hash
    db.close()


def test_old_linear_simulation_is_not_reinterpreted_as_price(monkeypatch):
    db, user, _ = _db_with_member()
    simulation = loans.simulate_loan(
        LoanSimulationIn(principal=Decimal("100"), installments=3), user, db
    )
    row = db.query(LoanSimulation).one()
    row.calculation_version = "linear_amortization_v1"
    db.commit()
    loans.confirm_simulation(
        LoanSimulationConfirmationIn(simulation_token=simulation["simulation_token"]),
        user,
        db,
    )
    monkeypatch.setattr(loans, "_loan_eligibility", lambda *args: _eligible())
    with pytest.raises(HTTPException, match="TERMS_MISMATCH"):
        loans.request_loan(
            LoanRequestIn(
                principal=Decimal("100"),
                installments=3,
                simulation_token=simulation["simulation_token"],
            ),
            user,
            db,
        )
    db.refresh(row)
    assert row.calculation_version == "linear_amortization_v1"
    assert row.status == "CONFIRMED"
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
