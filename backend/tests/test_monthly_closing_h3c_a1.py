import importlib.util
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import MonthlyClosing


MIGRATION_PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0087_monthly_closing_immutability_h3c_a1.py"
spec = importlib.util.spec_from_file_location("migration_0087", MIGRATION_PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def _engine():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def _apply(engine):
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()


def _revert(engine):
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()


def _closed(db, **overrides):
    values = {
        "competence": date(2026, 12, 1),
        "status": "CLOSED",
        "total_contributions": Decimal("100.00"),
        "total_expenses": Decimal("5.00"),
        "total_interest_received": Decimal("20.00"),
        "ledger_balance": Decimal("115.00"),
        "closed_by": None,
        "closed_at": datetime(2026, 12, 31, tzinfo=timezone.utc),
        "created_at": datetime(2026, 12, 1, tzinfo=timezone.utc),
        "snapshot_json": '{"closing_schema":"v0.40"}',
        "snapshot_hash": "a" * 64,
    }
    values.update(overrides)
    row = MonthlyClosing(**values)
    db.add(row)
    db.commit()
    return row


def test_migration_declares_direct_successor_and_both_dialect_strategies():
    assert migration.down_revision == "0086_payment_settlement_agreement_state_v106"
    source = MIGRATION_PATH.read_text()
    assert "bind.dialect.name == \"sqlite\"" in source
    assert "bind.dialect.name == \"postgresql\"" in source
    assert "RAISE(ABORT" in source
    assert "LANGUAGE plpgsql" in source


@pytest.mark.parametrize("column,value", [
    ("status", "OPEN"),
    ("competence", date(2027, 1, 1)),
    ("total_contributions", Decimal("1.00")),
    ("total_expenses", Decimal("1.00")),
    ("total_interest_received", Decimal("1.00")),
    ("ledger_balance", Decimal("1.00")),
    ("closed_by", 7),
    ("closed_at", datetime(2027, 1, 1, tzinfo=timezone.utc)),
    ("created_at", datetime(2027, 1, 1, tzinfo=timezone.utc)),
    ("snapshot_json", "{}"),
    ("snapshot_hash", "b" * 64),
])
def test_sqlite_blocks_every_update_of_closed_row(column, value):
    engine = _engine()
    _apply(engine)
    sessions = sessionmaker(bind=engine)
    db = sessions()
    row = _closed(db)
    before = {name: getattr(row, name) for name in (
        "competence", "status", "total_contributions", "total_expenses",
        "total_interest_received", "ledger_balance", "closed_by", "closed_at",
        "created_at", "snapshot_json", "snapshot_hash",
    )}

    setattr(row, column, value)
    with pytest.raises(IntegrityError, match="MonthlyClosing CLOSED é imutável"):
        db.commit()
    db.rollback()
    loaded = db.get(MonthlyClosing, row.id)
    assert {name: getattr(loaded, name) for name in before} == before


def test_sqlite_blocks_delete_of_closed_row_including_legacy_rows():
    engine = _engine()
    _apply(engine)
    db = sessionmaker(bind=engine)()
    modern = _closed(db)
    legacy = _closed(db, competence=date(2027, 1, 1), snapshot_json=None, snapshot_hash=None)

    db.delete(modern)
    with pytest.raises(IntegrityError, match="MonthlyClosing CLOSED é imutável"):
        db.commit()
    db.rollback()
    assert db.get(MonthlyClosing, modern.id) is not None

    db.delete(legacy)
    with pytest.raises(IntegrityError, match="MonthlyClosing CLOSED é imutável"):
        db.commit()
    db.rollback()
    assert db.get(MonthlyClosing, legacy.id) is not None


def test_sqlite_allows_open_update_and_delete():
    engine = _engine()
    _apply(engine)
    db = sessionmaker(bind=engine)()
    row = _closed(db, competence=date(2027, 2, 1), status="OPEN")

    row.total_contributions = Decimal("200.00")
    db.commit()
    row.status = "CLOSED"
    db.commit()
    with pytest.raises(IntegrityError):
        row.status = "OPEN"
        db.commit()
    db.rollback()

    db.delete(row)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.get(MonthlyClosing, row.id) is not None

    # A separate OPEN row proves the trigger does not block DELETE before close.
    open_row = _closed(db, competence=date(2027, 3, 1), status="OPEN", snapshot_hash=None)
    db.delete(open_row)
    db.commit()
    assert db.get(MonthlyClosing, open_row.id) is None


def test_upgrade_downgrade_and_reupgrade_toggle_sqlite_protection():
    engine = _engine()
    db = sessionmaker(bind=engine)()

    _apply(engine)
    names = {row[0] for row in db.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'")).fetchall()}
    assert {migration.SQLITE_UPDATE_TRIGGER, migration.SQLITE_DELETE_TRIGGER} <= names

    _revert(engine)
    names = {row[0] for row in db.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'")).fetchall()}
    assert migration.SQLITE_UPDATE_TRIGGER not in names
    assert migration.SQLITE_DELETE_TRIGGER not in names

    _apply(engine)
    names = {row[0] for row in db.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'")).fetchall()}
    assert {migration.SQLITE_UPDATE_TRIGGER, migration.SQLITE_DELETE_TRIGGER} <= names
