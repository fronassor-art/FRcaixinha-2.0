import os
import subprocess
import sys
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.api import loans
from app.db.base import Base
from app.models import Group, Loan, LoanInstallment, Member, User
from app.services.loan_amortization import (
    LOAN_CALCULATION_VERSION,
    PRICE_CALCULATION_VERSION,
    calculate_amortization,
)


def test_dispatcher_requires_explicit_supported_version():
    linear = calculate_amortization(
        LOAN_CALCULATION_VERSION, Decimal("150.00"), Decimal("0.20"), 6
    )
    price = calculate_amortization(
        PRICE_CALCULATION_VERSION, Decimal("150.00"), Decimal("0.20"), 6
    )
    assert linear[0][0]["amount"] == Decimal("55.00")
    assert price[0][0]["amount"] == Decimal("45.11")
    with pytest.raises(ValueError, match="Unknown loan calculation version"):
        calculate_amortization("unknown_v1", Decimal("150.00"), Decimal("0.20"), 6)
    with pytest.raises(ValueError, match="calculation_version is required"):
        calculate_amortization(None, Decimal("150.00"), Decimal("0.20"), 6)


def _approval_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    group = Group(name="Price approval group")
    admin = User(
        name="Price admin",
        email="price-admin@example.invalid",
        cpf="price-admin-cpf",
        password_hash="x",
        role="ADMIN",
    )
    db.add_all([group, admin])
    db.flush()
    member = Member(user_id=admin.id, group_id=group.id, status="ACTIVE")
    db.add(member)
    db.flush()
    loan = Loan(
        member_id=member.id,
        principal=Decimal("150.00"),
        monthly_rate=Decimal("0.20"),
        installments=6,
        calculation_version=PRICE_CALCULATION_VERSION,
        status="REQUESTED",
    )
    db.add(loan)
    db.commit()
    return db, admin, loan


def test_price_approval_uses_loan_version_and_persists_simulation_schedule(monkeypatch):
    db, admin, loan = _approval_db()
    monkeypatch.setattr(
        "app.services.approval_engine_v048.assert_loan_approval_allowed",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(loans, "create_notification", lambda *args, **kwargs: None)

    result = loans.decide_loan(
        loan.id,
        SimpleNamespace(approve=True, force_exception=False, admin_note=None),
        admin=admin,
        db=db,
    )

    assert result["status"] == "APPROVED"
    rows = (
        db.query(LoanInstallment)
        .filter(LoanInstallment.loan_id == loan.id)
        .order_by(LoanInstallment.number)
        .all()
    )
    assert [(row.principal, row.interest, row.amount) for row in rows] == [
        (Decimal("15.11"), Decimal("30.00"), Decimal("45.11")),
        (Decimal("18.13"), Decimal("26.98"), Decimal("45.11")),
        (Decimal("21.76"), Decimal("23.35"), Decimal("45.11")),
        (Decimal("26.11"), Decimal("19.00"), Decimal("45.11")),
        (Decimal("31.33"), Decimal("13.78"), Decimal("45.11")),
        (Decimal("37.56"), Decimal("7.51"), Decimal("45.07")),
    ]
    assert sum(row.principal for row in rows) == Decimal("150.00")
    assert sum(row.interest for row in rows) == Decimal("120.62")
    assert sum(row.amount for row in rows) == Decimal("270.62")
    assert db.get(Loan, loan.id).calculation_version == PRICE_CALCULATION_VERSION
    db.close()


def test_explicit_linear_loan_version_still_uses_linear_approval(monkeypatch):
    db, admin, loan = _approval_db()
    loan.calculation_version = LOAN_CALCULATION_VERSION
    db.commit()
    monkeypatch.setattr(
        "app.services.approval_engine_v048.assert_loan_approval_allowed",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(loans, "create_notification", lambda *args, **kwargs: None)

    loans.decide_loan(
        loan.id,
        SimpleNamespace(approve=True, force_exception=False, admin_note=None),
        admin=admin,
        db=db,
    )
    rows = (
        db.query(LoanInstallment)
        .filter(LoanInstallment.loan_id == loan.id)
        .order_by(LoanInstallment.number)
        .all()
    )
    assert [row.amount for row in rows] == [
        Decimal("55.00"), Decimal("50.00"), Decimal("45.00"),
        Decimal("40.00"), Decimal("35.00"), Decimal("30.00"),
    ]
    db.close()


def test_new_schedule_generation_rejects_null_loan_version(monkeypatch):
    db, admin, loan = _approval_db()
    loan.calculation_version = None
    db.commit()
    monkeypatch.setattr(loans, "create_notification", lambda *args, **kwargs: None)
    with pytest.raises(HTTPException, match="LOAN_CALCULATION_VERSION_REQUIRED"):
        loans.decide_loan(
            loan.id,
            SimpleNamespace(approve=True, force_exception=False, admin_note=None),
            admin=admin,
            db=db,
        )
    assert db.query(LoanInstallment).filter(LoanInstallment.loan_id == loan.id).count() == 0
    db.close()


def test_historical_null_version_with_persisted_installment_remains_readable():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    group = Group(name="Historical group")
    user = User(name="Historical", email="historical@example.invalid", cpf="historical-cpf", password_hash="x")
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
        status="ACTIVE",
        calculation_version=None,
    )
    db.add(loan)
    db.flush()
    db.add(LoanInstallment(
        loan_id=loan.id,
        number=1,
        due_date=date.today(),
        principal=Decimal("100.00"),
        interest=Decimal("20.00"),
        amount=Decimal("120.00"),
    ))
    db.commit()
    serialized = loans._serialize(loan, db)
    assert loan.calculation_version is None
    assert serialized["items"][0]["amount"] == "120.00"
    db.close()


def _alembic(database, *args):
    env = os.environ.copy()
    env.update({
        "DATABASE_URL": f"sqlite:///{database}",
        "JWT_SECRET": "testsecret",
        "APP_ENV": "test",
    })
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_0091_adds_nullable_loan_version_and_is_reversible(tmp_path):
    database = tmp_path / "loan_version.db"
    _alembic(database, "upgrade", "0090_member_financial_contribution_identity")
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.execute(text(
            "INSERT INTO loans "
            "(id, member_id, principal, principal_settled_with_own_balance, "
            "monthly_rate, installments, status, requested_at, state_revision) "
            "VALUES (1, 999, 100.00, 0.00, 0.20, 1, 'ACTIVE', "
            "'2026-01-01 00:00:00', 0)"
        ))

    _alembic(database, "upgrade", "0091_loan_calculation_version")
    with engine.begin() as connection:
        columns = {row[1]: row for row in connection.execute(text("PRAGMA table_info(loans)"))}
        assert "calculation_version" in columns
        assert columns["calculation_version"][3] == 0
        assert connection.execute(
            text("SELECT calculation_version FROM loans WHERE id = 1")
        ).scalar_one() is None

    _alembic(database, "downgrade", "0090_member_financial_contribution_identity")
    assert "calculation_version" not in {column["name"] for column in inspect(engine).get_columns("loans")}
    _alembic(database, "upgrade", "0091_loan_calculation_version")
    with engine.begin() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0091_loan_calculation_version"
