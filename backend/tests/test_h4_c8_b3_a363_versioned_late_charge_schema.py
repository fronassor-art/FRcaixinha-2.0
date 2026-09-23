import importlib.util
from io import StringIO
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateIndex, CreateTable

from app.core.loan_rules import (
    FINANCIAL_TIMEZONE,
    LATE_CHARGE_SETTLEMENT_COMPONENT_VERSION,
    LATE_CHARGE_VERSION,
)
from app.db.base import Base
from app.models import LoanInstallment, LoanLateChargeEvent, PaymentSettlement


REVISION = "0092_versioned_late_charge_foundation"
PREVIOUS_REVISION = "0091_loan_calculation_version"
HEAD_REVISION = "0096_cycle_foundation_a377a"
EVENT_TYPES = {
    "FIXED_PENALTY_ASSESSED",
    "LATE_INTEREST_ACCRUED",
    "PRINCIPAL_BASE_REDUCED",
    "PRINCIPAL_BASE_RESTORED",
}


def _backend_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _alembic(database: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{database}",
            "JWT_SECRET": "testsecret",
            "APP_ENV": "test",
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_backend_dir(),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _insert_legacy_rows(engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.execute(
            text(
                "INSERT INTO loans "
                "(id, member_id, principal, principal_settled_with_own_balance, "
                "monthly_rate, installments, calculation_version, status, requested_at, state_revision) "
                "VALUES (1, 999, 100.00, 0.00, 0.20, 1, NULL, 'ACTIVE', "
                "'2026-01-01 00:00:00', 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO loan_installments "
                "(id, loan_id, number, due_date, principal, interest, amount, paid_amount, "
                "penalty_amount, paid_penalty_amount, status, collection_stage, collection_attempts) "
                "VALUES (1, 1, 1, '2026-01-10', 100.00, 20.00, 120.00, 30.00, "
                "12.34, 2.34, 'PARTIAL', 'NORMAL', 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO payment_settlements "
                "(id, payment_id, member_id, obligation_type, loan_installment_id, "
                "amount_received, amount_applied, principal_applied, interest_applied, "
                "penalty_applied, excess_amount, obligation_status_before, "
                "obligation_status_after, confirmed_at, confirmation_source, receipt_number, "
                "receipt_version, receipt_snapshot_json, receipt_hash, created_at) "
                "VALUES (1, 999, 999, 'LOAN_INSTALLMENT', 1, "
                "45.00, 45.00, 15.00, 20.00, 10.00, 0.00, 'OVERDUE', 'PARTIAL', "
                "'2026-01-20 12:00:00', 'TEST', 'legacy-receipt', 'v1', '{}', "
                "'legacy-hash', '2026-01-20 12:00:00')"
            )
        )


def _insert_installment(connection, *, row_id: int = 1) -> None:
    connection.execute(
        text(
            "INSERT INTO loan_installments "
            "(id, loan_id, number, due_date, principal, interest, amount, paid_amount, "
            "penalty_amount, paid_penalty_amount, status, collection_stage, collection_attempts) "
            "VALUES (:id, 999, :number, '2026-01-10', 100.00, 20.00, 120.00, 0.00, "
            "0.00, 0.00, 'OPEN', 'NORMAL', 0)"
        ),
        {"id": row_id, "number": row_id},
    )


def test_a363_constants_models_and_single_head_contract():
    backend_dir = _backend_dir()
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    script = ScriptDirectory.from_config(config)

    assert script.get_current_head() == HEAD_REVISION
    assert script.get_revision(REVISION).down_revision == PREVIOUS_REVISION
    assert len(list(script.walk_revisions())) == 102
    assert LATE_CHARGE_VERSION == "late_charge_daily_simple_v1"
    assert LATE_CHARGE_SETTLEMENT_COMPONENT_VERSION == "late_charge_components_v1"
    assert FINANCIAL_TIMEZONE == "America/Belem"

    installment_columns = LoanInstallment.__table__.c
    assert installment_columns.late_charge_version.nullable
    assert installment_columns.fixed_penalty_amount.type.python_type is Decimal
    assert installment_columns.paid_fixed_penalty_amount.type.python_type is Decimal
    assert installment_columns.late_interest_amount.type.python_type is Decimal
    assert installment_columns.paid_late_interest_amount.type.python_type is Decimal
    assert installment_columns.late_interest_accrued_through_date.type.python_type is date

    settlement_columns = PaymentSettlement.__table__.c
    assert settlement_columns.settlement_component_version.nullable
    assert settlement_columns.normal_interest_applied.type.python_type is Decimal
    assert settlement_columns.late_interest_applied.type.python_type is Decimal
    assert settlement_columns.fixed_penalty_applied.type.python_type is Decimal

    event_check = next(
        constraint
        for constraint in LoanLateChargeEvent.__table__.constraints
        if constraint.name == "ck_loan_late_charge_events_type"
    )
    assert all(event_type in str(event_check.sqltext) for event_type in EVENT_TYPES)


def test_fresh_upgrade_historical_rows_and_reversible_chain(tmp_path):
    database = tmp_path / "a363_history.db"
    _alembic(database, "upgrade", PREVIOUS_REVISION)
    engine = create_engine(f"sqlite:///{database}")
    _insert_legacy_rows(engine)

    _alembic(database, "upgrade", REVISION)
    columns = {column["name"]: column for column in inspect(engine).get_columns("loan_installments")}
    assert columns["late_charge_version"]["nullable"]
    assert columns["fixed_penalty_amount"]["type"].python_type is Decimal
    settlement_columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("payment_settlements")
    }

    with engine.connect() as connection:
        installment = connection.execute(
            text(
                "SELECT penalty_amount, paid_penalty_amount, late_charge_version, "
                "fixed_penalty_amount, paid_fixed_penalty_amount, late_interest_amount, "
                "paid_late_interest_amount, late_interest_accrued_through_date "
                "FROM loan_installments WHERE id = 1"
            )
        ).mappings().one()
        settlement = connection.execute(
            text(
                "SELECT interest_applied, penalty_applied, settlement_component_version, "
                "normal_interest_applied, late_interest_applied, fixed_penalty_applied "
                "FROM payment_settlements WHERE id = 1"
            )
        ).mappings().one()
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == REVISION

    assert Decimal(str(installment["penalty_amount"])) == Decimal("12.34")
    assert Decimal(str(installment["paid_penalty_amount"])) == Decimal("2.34")
    assert all(
        installment[name] is None
        for name in (
            "late_charge_version",
            "fixed_penalty_amount",
            "paid_fixed_penalty_amount",
            "late_interest_amount",
            "paid_late_interest_amount",
            "late_interest_accrued_through_date",
        )
    )
    assert Decimal(str(settlement["interest_applied"])) == Decimal("20.00")
    assert Decimal(str(settlement["penalty_applied"])) == Decimal("10.00")
    assert all(
        settlement[name] is None
        for name in (
            "settlement_component_version",
            "normal_interest_applied",
            "late_interest_applied",
            "fixed_penalty_applied",
        )
    )
    assert settlement_columns["normal_interest_applied"]["nullable"]

    engine.dispose()
    _alembic(database, "downgrade", PREVIOUS_REVISION)
    downgraded = create_engine(f"sqlite:///{database}")
    assert "late_charge_version" not in {
        column["name"] for column in inspect(downgraded).get_columns("loan_installments")
    }
    assert "settlement_component_version" not in {
        column["name"] for column in inspect(downgraded).get_columns("payment_settlements")
    }
    assert not inspect(downgraded).has_table("loan_late_charge_events")
    downgraded.dispose()

    _alembic(database, "upgrade", REVISION)
    upgraded = create_engine(f"sqlite:///{database}")
    with upgraded.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == REVISION
    upgraded.dispose()


def test_fresh_upgrade_to_head(tmp_path):
    database = tmp_path / "a363_fresh.db"
    _alembic(database, "upgrade", "head")
    engine = create_engine(f"sqlite:///{database}")
    assert inspect(engine).has_table("loan_late_charge_events")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == HEAD_REVISION
    engine.dispose()


def test_model_constraints_and_no_automatic_activation_or_events():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with engine.begin() as connection:
        _insert_installment(connection)
        row = connection.execute(
            text(
                "SELECT late_charge_version, fixed_penalty_amount, late_interest_amount "
                "FROM loan_installments WHERE id = 1"
            )
        ).mappings().one()
        assert row == {
            "late_charge_version": None,
            "fixed_penalty_amount": None,
            "late_interest_amount": None,
        }
        assert connection.execute(
            text("SELECT COUNT(*) FROM loan_late_charge_events")
        ).scalar_one() == 0

        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "UPDATE loan_installments SET late_charge_version = :version, "
                    "fixed_penalty_amount = -0.01, paid_fixed_penalty_amount = 0, "
                    "late_interest_amount = 0, paid_late_interest_amount = 0 WHERE id = 1"
                ),
                {"version": LATE_CHARGE_VERSION},
            )


def test_fixed_penalty_uniqueness_and_event_type_coexistence():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)

    with engine.begin() as connection:
        _insert_installment(connection)
        values = {
            "installment": 1,
            "version": LATE_CHARGE_VERSION,
            "effective": date(2026, 1, 11),
            "created": now,
        }
        connection.execute(
            LoanLateChargeEvent.__table__.insert().values(
                loan_installment_id=values["installment"],
                late_charge_version=values["version"],
                event_type="FIXED_PENALTY_ASSESSED",
                effective_date=values["effective"],
                created_at=values["created"],
                amount=Decimal("10.00"),
                eligible_principal=Decimal("100.00"),
            )
        )
        connection.execute(
            LoanLateChargeEvent.__table__.insert().values(
                loan_installment_id=1,
                late_charge_version=LATE_CHARGE_VERSION,
                event_type="LATE_INTEREST_ACCRUED",
                effective_date=date(2026, 1, 11),
                created_at=now,
                amount=Decimal("0.67"),
                eligible_principal=Decimal("100.00"),
            )
        )
        connection.execute(
            LoanLateChargeEvent.__table__.insert().values(
                loan_installment_id=1,
                late_charge_version="late_charge_daily_simple_v2",
                event_type="FIXED_PENALTY_ASSESSED",
                effective_date=date(2026, 1, 11),
                created_at=now,
                amount=Decimal("10.00"),
            )
        )

        with pytest.raises(IntegrityError):
            connection.execute(
                LoanLateChargeEvent.__table__.insert().values(
                    loan_installment_id=1,
                    late_charge_version=LATE_CHARGE_VERSION,
                    event_type="FIXED_PENALTY_ASSESSED",
                    effective_date=date(2026, 1, 12),
                    created_at=now,
                    amount=Decimal("10.00"),
                )
            )


def test_versioned_settlement_component_contract():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.execute(
            text(
                "INSERT INTO payment_settlements "
                "(id, payment_id, member_id, obligation_type, loan_installment_id, "
                "amount_received, amount_applied, principal_applied, interest_applied, "
                "penalty_applied, settlement_component_version, normal_interest_applied, "
                "late_interest_applied, fixed_penalty_applied, excess_amount, "
                "obligation_status_before, obligation_status_after, confirmed_at, "
                "confirmation_source, receipt_number, receipt_version, receipt_snapshot_json, "
                "receipt_hash, created_at) VALUES "
                "(1, 1, 1, 'LOAN_INSTALLMENT', 1, 45.00, 45.00, 15.00, 20.00, "
                "10.00, :version, 20.00, 2.00, 8.00, 0.00, 'OVERDUE', 'PARTIAL', "
                "'2026-01-20 12:00:00', 'TEST', 'new-receipt', 'v1', '{}', "
                "'new-hash', '2026-01-20 12:00:00')"
            ),
            {"version": LATE_CHARGE_SETTLEMENT_COMPONENT_VERSION},
        )

        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "UPDATE payment_settlements SET late_interest_applied = 2.01 "
                    "WHERE id = 1"
                )
            )


def test_postgresql_ddl_compiles_partial_unique_index():
    table_sql = str(
        CreateTable(LoanLateChargeEvent.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    index = next(
        index
        for index in LoanLateChargeEvent.__table__.indexes
        if index.name == "uq_loan_late_charge_events_fixed_penalty"
    )
    index_sql = str(CreateIndex(index).compile(dialect=postgresql.dialect()))

    assert "loan_late_charge_events" in table_sql
    assert "WHERE event_type = 'FIXED_PENALTY_ASSESSED'" in index_sql


def test_postgresql_migration_upgrade_ddl_compiles():
    migration_path = (
        _backend_dir()
        / "alembic"
        / "versions"
        / "0092_versioned_late_charge_foundation.py"
    )
    spec = importlib.util.spec_from_file_location("a363_migration_0092", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    migration.op = Operations(context)
    migration.upgrade()
    ddl = output.getvalue()

    assert "ALTER TABLE loan_installments ADD COLUMN late_charge_version" in ddl
    assert "ALTER TABLE payment_settlements ADD COLUMN settlement_component_version" in ddl
    assert "CREATE TABLE loan_late_charge_events" in ddl
    assert "WHERE event_type = 'FIXED_PENALTY_ASSESSED'" in ddl
