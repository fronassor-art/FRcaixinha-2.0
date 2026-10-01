"""PostgreSQL-specific migration and check constraint proof for F1."""

import importlib.util
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import LedgerEntry
from app.services.ledger import _hash_payload, _hash_payload_v2, post_entry, verify_ledger_chain


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = BACKEND_ROOT / "alembic" / "versions" / "0105_ledger_financial_date_f1.py"


def _safe_test_url():
    raw_url = os.environ.get("DATABASE_URL", "")
    try:
        url = sa.engine.make_url(raw_url)
    except Exception:
        return None
    if (
        os.environ.get("APP_ENV") != "test"
        or not url.drivername.startswith("postgresql")
        or url.host not in {"127.0.0.1", "localhost"}
        or url.port != 5432
        or url.database != "frcaixinha_test"
        or url.username != "frcaixinha_test"
    ):
        return None
    return url


def test_postgresql_migration_constraint_and_mixed_hash_chain():
    url = _safe_test_url()
    if url is None:
        pytest.skip("requires the isolated PostgreSQL 16 frcaixinha_test CI service")

    spec = importlib.util.spec_from_file_location("ledger_financial_date_f1_pg", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TEMP TABLE ledger_entries ("
                "id SERIAL PRIMARY KEY, account VARCHAR(80) NOT NULL, direction VARCHAR(10) NOT NULL, "
                "amount NUMERIC(14,2) NOT NULL, reference_type VARCHAR(50) NOT NULL, "
                "reference_id VARCHAR(80) NOT NULL, reversal_of_id INTEGER REFERENCES ledger_entries(id), "
                "previous_hash VARCHAR(64), entry_hash VARCHAR(64) UNIQUE, created_at TIMESTAMPTZ NOT NULL)"
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

            inspector = sa.inspect(connection)
            columns = {column["name"]: column["type"] for column in inspector.get_columns("ledger_entries")}
            assert isinstance(columns["financial_date"], sa.Date)
            assert isinstance(columns["hash_version"], sa.SmallInteger)
            assert "ix_ledger_entries_financial_date" in {
                index["name"] for index in inspector.get_indexes("ledger_entries")
            }

            with Session(bind=connection) as db:
                v1 = post_entry(db, "CAIXINHA", "CREDIT", Decimal("4.00"), "F1_PG", "legacy")
                db.flush()
                v2 = LedgerEntry(
                    account="CAIXINHA", direction="DEBIT", amount=Decimal("1.00"),
                    reference_type="F1_PG", reference_id="v2", reversal_of_id=None,
                    previous_hash=v1.entry_hash, created_at=datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc),
                    financial_date=date(2026, 9, 30), hash_version=2,
                )
                v2.entry_hash = _hash_payload_v2(v2, v1.entry_hash)
                db.add(v2)
                db.flush()
                assert verify_ledger_chain(db)["status"] == "PASS"

                with pytest.raises(IntegrityError):
                    with db.begin_nested():
                        db.add(LedgerEntry(
                            account="CAIXINHA", direction="CREDIT", amount=Decimal("1.00"),
                            reference_type="F1_PG", reference_id="bad-pair", reversal_of_id=None,
                            previous_hash=v2.entry_hash, created_at=datetime.now(timezone.utc),
                            financial_date=date(2026, 10, 1), hash_version=None,
                        ))
                        db.flush()
                db.expire_all()
                assert verify_ledger_chain(db)["status"] == "PASS"

            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(RuntimeError, match="versioned ledger rows"):
                    migration.downgrade()
            assert {column["name"] for column in sa.inspect(connection).get_columns("ledger_entries")} >= {
                "financial_date", "hash_version",
            }
    finally:
        engine.dispose()


def test_postgresql_populated_v1_chain_survives_upgrade_and_legacy_downgrade():
    url = _safe_test_url()
    if url is None:
        pytest.skip("requires the isolated PostgreSQL 16 frcaixinha_test CI service")

    spec = importlib.util.spec_from_file_location("ledger_financial_date_f1_pg_legacy", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(url)
    created_at_values = [
        datetime(2026, 9, 16, 3, 51, tzinfo=timezone.utc),
        datetime(2026, 9, 16, 4, 0, tzinfo=timezone.utc),
    ]
    entries = [
        SimpleNamespace(
            account="CAIXINHA", direction="CREDIT", amount=Decimal("12.30"),
            reference_type="F1_PG_LEGACY", reference_id="legacy-1",
            reversal_of_id=None, created_at=created_at_values[0],
        ),
        SimpleNamespace(
            account="CAIXINHA", direction="DEBIT", amount=Decimal("2.30"),
            reference_type="F1_PG_LEGACY", reference_id="legacy-2",
            reversal_of_id=None, created_at=created_at_values[1],
        ),
    ]
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TEMP TABLE ledger_entries ("
                "id SERIAL PRIMARY KEY, account VARCHAR(80) NOT NULL, direction VARCHAR(10) NOT NULL, "
                "amount NUMERIC(14,2) NOT NULL, reference_type VARCHAR(50) NOT NULL, "
                "reference_id VARCHAR(80) NOT NULL, reversal_of_id INTEGER REFERENCES ledger_entries(id), "
                "previous_hash VARCHAR(64), entry_hash VARCHAR(64) UNIQUE, created_at TIMESTAMPTZ NOT NULL)"
            )
            previous_hash = None
            expected_hashes = []
            for entry_id, entry in enumerate(entries, start=1):
                entry.entry_hash = _hash_payload(entry, previous_hash)
                expected_hashes.append(entry.entry_hash)
                connection.execute(
                    sa.text(
                        "INSERT INTO ledger_entries "
                        "(id, account, direction, amount, reference_type, reference_id, reversal_of_id, "
                        "previous_hash, entry_hash, created_at) "
                        "VALUES (:id, :account, :direction, :amount, :reference_type, :reference_id, "
                        ":reversal_of_id, :previous_hash, :entry_hash, :created_at)"
                    ),
                    {
                        "id": entry_id,
                        "account": entry.account,
                        "direction": entry.direction,
                        "amount": entry.amount,
                        "reference_type": entry.reference_type,
                        "reference_id": entry.reference_id,
                        "reversal_of_id": entry.reversal_of_id,
                        "previous_hash": previous_hash,
                        "entry_hash": entry.entry_hash,
                        "created_at": entry.created_at,
                    },
                )
                previous_hash = entry.entry_hash

            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

            with Session(bind=connection) as db:
                rows = db.query(LedgerEntry).order_by(LedgerEntry.id.asc()).all()
                assert len(rows) == 2
                assert [(row.financial_date, row.hash_version) for row in rows] == [(None, None), (None, None)]
                assert [row.entry_hash for row in rows] == expected_hashes
                assert verify_ledger_chain(db) == {"status": "PASS", "entries": 2, "errors": []}

            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()

            columns = {column["name"] for column in sa.inspect(connection).get_columns("ledger_entries")}
            assert "financial_date" not in columns
            assert "hash_version" not in columns
            restored_rows = connection.execute(
                sa.text(
                    "SELECT id, previous_hash, entry_hash, created_at "
                    "FROM ledger_entries ORDER BY id"
                )
            ).all()
            assert [(row.id, row.previous_hash, row.entry_hash, row.created_at) for row in restored_rows] == [
                (1, None, expected_hashes[0], created_at_values[0]),
                (2, expected_hashes[0], expected_hashes[1], created_at_values[1]),
            ]
    finally:
        engine.dispose()
