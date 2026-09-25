import os
import subprocess
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.models import LoanLateChargeEvent


HEAD_REVISION = "0101_member_payout_destination_a377b4r2"
REVISION = "0094_late_interest_event_contract"
DOWN_REVISION = "0093_payment_attempt_schema_foundation"
ADJUSTMENT_TYPES = {
    "LATE_INTEREST_ADJUSTMENT_INCREASE",
    "LATE_INTEREST_ADJUSTMENT_DECREASE",
}
OLD_EVENT_TYPES = {
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


def _insert_installment(connection, row_id=1):
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


def _event_values(**overrides):
    values = {
        "loan_installment_id": 1,
        "late_charge_version": "late_charge_daily_simple_v1",
        "event_type": "LATE_INTEREST_ACCRUED",
        "effective_date": date(2026, 1, 11),
        "created_at": datetime.now(timezone.utc),
        "amount": Decimal("0.67"),
        "eligible_principal": Decimal("100.00"),
        "payment_settlement_id": None,
        "payment_reversal_id": None,
    }
    values.update(overrides)
    return values


def _memory_engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        _insert_installment(connection)
    return engine


def test_single_head_and_revision_chain():
    config = Config(str(_backend_dir() / "alembic.ini"))
    config.set_main_option("script_location", str(_backend_dir() / "alembic"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == [HEAD_REVISION]
    assert (
        script.get_revision(HEAD_REVISION).down_revision
        == "0099_cycle_closing_review_a377b3r1"
    )
    assert (
        script.get_revision("0095_pix_attempt_provider_reservation").down_revision
        == REVISION
    )
    assert script.get_revision(REVISION).down_revision == DOWN_REVISION


def test_upgrade_and_downgrade_sqlite_without_adjustments(tmp_path):
    database = tmp_path / "contract.db"
    _alembic(database, "upgrade", REVISION)
    engine = create_engine(f"sqlite:///{database}")
    indexes = {index["name"] for index in inspect(engine).get_indexes("loan_late_charge_events")}
    assert {
        "uq_llce_late_interest_day",
        "uq_llce_adjustment_settlement",
        "uq_llce_adjustment_reversal",
    } <= indexes
    engine.dispose()
    _alembic(database, "downgrade", DOWN_REVISION)
    engine = create_engine(f"sqlite:///{database}")
    indexes = {index["name"] for index in inspect(engine).get_indexes("loan_late_charge_events")}
    assert "uq_llce_late_interest_day" not in indexes
    assert "uq_llce_adjustment_settlement" not in indexes
    assert "uq_llce_adjustment_reversal" not in indexes
    engine.dispose()


def test_downgrade_is_blocked_by_adjustment_data(tmp_path):
    database = tmp_path / "guard.db"
    _alembic(database, "upgrade", REVISION)
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        _insert_installment(connection)
        connection.execute(
            LoanLateChargeEvent.__table__.insert().values(
                **_event_values(
                    event_type="LATE_INTEREST_ADJUSTMENT_DECREASE",
                    amount=Decimal("0.10"),
                    eligible_principal=None,
                    payment_settlement_id=123,
                )
            )
        )
    engine.dispose()
    with pytest.raises(subprocess.CalledProcessError):
        _alembic(database, "downgrade", DOWN_REVISION)


def test_model_declares_new_contract_and_legacy_types():
    event_check = next(
        constraint
        for constraint in LoanLateChargeEvent.__table__.constraints
        if constraint.name == "ck_loan_late_charge_events_type"
    )
    event_sql = str(event_check.sqltext)
    assert all(event_type in event_sql for event_type in OLD_EVENT_TYPES | ADJUSTMENT_TYPES)
    names = {index.name for index in LoanLateChargeEvent.__table__.indexes}
    assert {
        "uq_llce_late_interest_day",
        "uq_llce_adjustment_settlement",
        "uq_llce_adjustment_reversal",
    } <= names


@pytest.mark.parametrize("event_type", sorted(OLD_EVENT_TYPES))
def test_legacy_event_types_remain_valid(event_type):
    engine = _memory_engine()
    with engine.begin() as connection:
        connection.execute(
            LoanLateChargeEvent.__table__.insert().values(
                **_event_values(
                    event_type=event_type,
                    amount=Decimal("0.10") if event_type != "PRINCIPAL_BASE_REDUCED" and event_type != "PRINCIPAL_BASE_RESTORED" else None,
                    eligible_principal=Decimal("100.00") if event_type == "LATE_INTEREST_ACCRUED" else None,
                    effective_date=date(2026, 1, 11),
                )
            )
        )
    engine.dispose()


@pytest.mark.parametrize("event_type", sorted(ADJUSTMENT_TYPES))
def test_adjustment_contract_and_causal_uniques(event_type):
    engine = _memory_engine()
    with engine.begin() as connection:
        connection.execute(
            LoanLateChargeEvent.__table__.insert().values(
                **_event_values(
                    event_type=event_type,
                    eligible_principal=None,
                    payment_settlement_id=101,
                )
            )
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                LoanLateChargeEvent.__table__.insert().values(
                    **_event_values(
                        event_type=(
                            "LATE_INTEREST_ADJUSTMENT_INCREASE"
                            if event_type == "LATE_INTEREST_ADJUSTMENT_DECREASE"
                            else "LATE_INTEREST_ADJUSTMENT_DECREASE"
                        ),
                        effective_date=date(2026, 1, 12),
                        eligible_principal=None,
                        payment_settlement_id=101,
                    )
                )
            )
    engine.dispose()


def test_reversal_adjustment_duplicate_is_rejected_across_directions():
    engine = _memory_engine()
    with engine.begin() as connection:
        connection.execute(
            LoanLateChargeEvent.__table__.insert().values(
                **_event_values(
                    event_type="LATE_INTEREST_ADJUSTMENT_DECREASE",
                    eligible_principal=None,
                    payment_reversal_id=401,
                )
            )
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                LoanLateChargeEvent.__table__.insert().values(
                    **_event_values(
                        event_type="LATE_INTEREST_ADJUSTMENT_INCREASE",
                        effective_date=date(2026, 1, 12),
                        eligible_principal=None,
                        payment_reversal_id=401,
                    )
                )
            )
    engine.dispose()


def test_daily_accrual_unique_and_distinct_dates():
    engine = _memory_engine()
    with engine.begin() as connection:
        connection.execute(LoanLateChargeEvent.__table__.insert().values(**_event_values()))
        with pytest.raises(IntegrityError):
            connection.execute(LoanLateChargeEvent.__table__.insert().values(**_event_values()))
        connection.execute(
            LoanLateChargeEvent.__table__.insert().values(
                **_event_values(effective_date=date(2026, 1, 12))
            )
        )
    engine.dispose()


def test_invalid_event_type_is_rejected():
    engine = _memory_engine()
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                LoanLateChargeEvent.__table__.insert().values(
                    **_event_values(event_type="NOT_A_LATE_CHARGE_EVENT")
                )
            )
    engine.dispose()


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": Decimal("-0.01")},
        {"event_type": "LATE_INTEREST_ADJUSTMENT_INCREASE", "amount": None, "eligible_principal": None, "payment_settlement_id": 1},
        {"event_type": "LATE_INTEREST_ADJUSTMENT_INCREASE", "eligible_principal": Decimal("1.00"), "payment_settlement_id": 1},
        {"event_type": "LATE_INTEREST_ADJUSTMENT_INCREASE"},
        {"event_type": "LATE_INTEREST_ADJUSTMENT_INCREASE", "payment_settlement_id": 1, "payment_reversal_id": 2},
    ],
)
def test_invalid_values_and_causality_are_rejected(overrides):
    engine = _memory_engine()
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                LoanLateChargeEvent.__table__.insert().values(
                    **_event_values(**overrides)
                )
            )
    engine.dispose()


def test_distinct_settlement_and_reversal_causes_are_accepted():
    engine = _memory_engine()
    with engine.begin() as connection:
        connection.execute(
            LoanLateChargeEvent.__table__.insert().values(
                **_event_values(
                    event_type="LATE_INTEREST_ADJUSTMENT_DECREASE",
                    eligible_principal=None,
                    payment_settlement_id=201,
                )
            )
        )
        connection.execute(
            LoanLateChargeEvent.__table__.insert().values(
                **_event_values(
                    event_type="LATE_INTEREST_ADJUSTMENT_INCREASE",
                    effective_date=date(2026, 1, 12),
                    eligible_principal=None,
                    payment_reversal_id=301,
                )
            )
        )
    engine.dispose()
