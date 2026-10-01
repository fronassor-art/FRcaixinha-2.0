"""Schema-only foundation for persisted event financial identity."""

import importlib.util
from datetime import date, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from app.models import PaymentReversal, PaymentSettlement


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    BACKEND_ROOT / "alembic" / "versions" / "0106_event_financial_date_f2e1.py"
)
REVISION = "0106_event_financial_date_f2e1"
PARENT = "0105_ledger_financial_date_f1"


def _load_migration():
    spec = importlib.util.spec_from_file_location("event_financial_date_f2e1", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _engine():
    return sa.create_engine("sqlite:///:memory:")


def _legacy_tables(engine):
    metadata = sa.MetaData()
    settlements = sa.Table(
        "payment_settlements",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("receipt_version", sa.String(20), nullable=False),
        sa.Column("receipt_snapshot_json", sa.Text(), nullable=False),
        sa.Column("receipt_hash", sa.String(64), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
    )
    reversals = sa.Table(
        "payment_reversals",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("receipt_version", sa.String(20), nullable=False),
        sa.Column("receipt_snapshot_json", sa.Text(), nullable=False),
        sa.Column("receipt_hash", sa.String(64), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(engine)
    return settlements, reversals


def _apply(connection, method):
    migration = _load_migration()
    with Operations.context(MigrationContext.configure(connection)):
        getattr(migration, method)()


def test_upgrade_adds_nullable_dates_without_backfill_or_legacy_mutation():
    engine = _engine()
    settlements, reversals = _legacy_tables(engine)
    with engine.begin() as connection:
        connection.execute(settlements.insert(), {
            "id": 1, "receipt_version": "v1", "receipt_snapshot_json": '{"legacy":true}',
            "receipt_hash": "settlement-hash", "confirmed_at": datetime(2026, 9, 30, 23, 55),
        })
        connection.execute(reversals.insert(), {
            "id": 1, "receipt_version": "v1", "receipt_snapshot_json": '{"legacy":true}',
            "receipt_hash": "reversal-hash", "reversed_at": datetime(2026, 10, 1, 0, 5),
        })
        _apply(connection, "upgrade")
        for table, expected in (("payment_settlements", "settlement-hash"), ("payment_reversals", "reversal-hash")):
            columns = {column["name"]: column for column in sa.inspect(connection).get_columns(table)}
            assert isinstance(columns["financial_date"]["type"], sa.Date)
            assert columns["financial_date"]["nullable"] is True
            row = connection.execute(sa.text(
                f"SELECT receipt_version, receipt_snapshot_json, receipt_hash, financial_date FROM {table} WHERE id = 1"
            )).one()
            assert tuple(row) == ("v1", '{"legacy":true}', expected, None)
    engine.dispose()


def test_orm_maps_legacy_null_and_accepts_date_without_writer_side_effect():
    assert PaymentSettlement.__table__.c.financial_date.nullable is True
    assert isinstance(PaymentSettlement.__table__.c.financial_date.type, sa.Date)
    assert PaymentReversal.__table__.c.financial_date.nullable is True
    assert isinstance(PaymentReversal.__table__.c.financial_date.type, sa.Date)

    engine = _engine()
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE payment_settlements (id INTEGER PRIMARY KEY, financial_date DATE NULL)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE payment_reversals (id INTEGER PRIMARY KEY, financial_date DATE NULL)"
        )
        connection.exec_driver_sql("INSERT INTO payment_settlements (id, financial_date) VALUES (1, NULL)")
        connection.exec_driver_sql("INSERT INTO payment_reversals (id, financial_date) VALUES (1, NULL)")
        assert connection.execute(sa.select(PaymentSettlement.financial_date)).scalar_one() is None
        assert connection.execute(sa.select(PaymentReversal.financial_date)).scalar_one() is None

    settlement = PaymentSettlement(financial_date=None)
    reversal = PaymentReversal(financial_date=None)
    day = date(2026, 9, 30)
    settlement.financial_date = day
    reversal.financial_date = day
    assert settlement.financial_date == day
    assert reversal.financial_date == day
    engine.dispose()


@pytest.mark.parametrize("table", ["payment_settlements", "payment_reversals"])
def test_downgrade_refuses_to_discard_any_event_financial_date(table):
    engine = _engine()
    settlements, reversals = _legacy_tables(engine)
    with engine.begin() as connection:
        connection.execute(settlements.insert(), {
            "id": 1, "receipt_version": "v1", "receipt_snapshot_json": "{}",
            "receipt_hash": "s-hash", "confirmed_at": datetime(2026, 9, 30, 23, 55),
        })
        connection.execute(reversals.insert(), {
            "id": 1, "receipt_version": "v1", "receipt_snapshot_json": "{}",
            "receipt_hash": "r-hash", "reversed_at": datetime(2026, 10, 1, 0, 5),
        })
        _apply(connection, "upgrade")
        connection.execute(sa.text(
            f"UPDATE {table} SET financial_date = '2026-09-30' WHERE id = 1"
        ))
        with pytest.raises(RuntimeError, match=f"{table} contains financial-date values"):
            _apply(connection, "downgrade")
        assert "financial_date" in {
            column["name"] for column in sa.inspect(connection).get_columns(table)
        }
    engine.dispose()


def test_downgrade_upgrade_round_trip_with_only_null_dates():
    engine = _engine()
    settlements, reversals = _legacy_tables(engine)
    with engine.begin() as connection:
        connection.execute(settlements.insert(), {
            "id": 1, "receipt_version": "v1", "receipt_snapshot_json": "{}",
            "receipt_hash": "s-hash", "confirmed_at": datetime(2026, 9, 30, 23, 55),
        })
        connection.execute(reversals.insert(), {
            "id": 1, "receipt_version": "v1", "receipt_snapshot_json": "{}",
            "receipt_hash": "r-hash", "reversed_at": datetime(2026, 10, 1, 0, 5),
        })
        _apply(connection, "upgrade")
        _apply(connection, "downgrade")
        for table in ("payment_settlements", "payment_reversals"):
            assert "financial_date" not in {
                column["name"] for column in sa.inspect(connection).get_columns(table)
            }
        _apply(connection, "upgrade")
        for table in ("payment_settlements", "payment_reversals"):
            assert "financial_date" in {
                column["name"] for column in sa.inspect(connection).get_columns(table)
            }
    engine.dispose()


def test_migration_is_single_successor_of_f1():
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_current_head() == REVISION
    assert tuple(scripts.get_heads()) == (REVISION,)
    assert scripts.get_revision(REVISION).down_revision == PARENT
