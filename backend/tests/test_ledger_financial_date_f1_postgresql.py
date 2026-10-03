"""PostgreSQL-specific migration and check constraint proof for F1."""
from test_f2g3a4p_real_writer_lock_proof import TestF2G3A4PRealWriterLockProof
from test_f2g3a3_postgres_lock_protocol_proof import TestF2G3A3PostgresLockProtocolProof

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
from app.services.ledger import _hash_payload, post_entry, post_entry_v2, verify_ledger_chain


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
                v2 = post_entry_v2(
                    db, "CAIXINHA", "DEBIT", Decimal("1.00"), "F1_PG", "v2",
                    financial_date=date(2026, 9, 30),
                )
                db.flush()
                assert v2.previous_hash == v1.entry_hash
                assert v2.created_at.tzinfo is not None
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


@pytest.fixture
def sequencing_engine():
    """Isolated shared table for two real PostgreSQL connections; no migrations."""
    import uuid

    url = _safe_test_url()
    if url is None:
        pytest.skip("requires the isolated PostgreSQL 16 frcaixinha_test CI service")
    engine = sa.create_engine(url)
    schema = "ledger_sequence_" + uuid.uuid4().hex
    try:
        with engine.begin() as connection:
            assert int(connection.exec_driver_sql("SHOW server_version_num").scalar_one()) // 10000 == 16
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        isolated = engine.execution_options(schema_translate_map={None: schema})
        with isolated.begin() as connection:
            LedgerEntry.__table__.create(connection)
        yield isolated
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        engine.dispose()


def test_postgresql_sequencing_rollback_and_constraints(sequencing_engine):
    with Session(sequencing_engine, autoflush=False) as db:
        first = post_entry_v2(
            db, "CAIXINHA", "CREDIT", Decimal("4.00"), "SEQUENCE", "rollback-1",
            financial_date=date(2026, 9, 30),
        )
        second = post_entry_v2(
            db, "CAIXINHA", "DEBIT", Decimal("1.00"), "SEQUENCE", "rollback-2",
            financial_date=date(2026, 10, 1),
        )
        assert first.id is not None and second.id is not None
        assert second.previous_hash == first.entry_hash
        assert verify_ledger_chain(db)["status"] == "PASS"
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(LedgerEntry(
                    account="CAIXINHA", direction="CREDIT", amount=Decimal("1.00"),
                    reference_type="SEQUENCE", reference_id="bad-pair",
                    financial_date=date(2026, 10, 1), hash_version=None,
                    created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                    entry_hash="f" * 64,
                ))
                db.flush()
        assert verify_ledger_chain(db)["status"] == "PASS"
        db.rollback()
    with Session(sequencing_engine, autoflush=False) as observer:
        assert observer.query(LedgerEntry).count() == 0


def test_postgresql_concurrent_transactions_serialize_multiple_posts(sequencing_engine):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    first_ready = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    second_pid = []

    def append_pair(label, *, holder):
        with Session(sequencing_engine, autoflush=False) as db:
            db.execute(sa.text("SET LOCAL statement_timeout = '15000ms'"))
            if not holder:
                second_pid.append(db.execute(sa.text("SELECT pg_backend_pid()")).scalar_one())
                second_started.set()
            first = post_entry(db, "CAIXINHA", "CREDIT", Decimal("4.00"), "SEQUENCE", label + "-v1")
            second = post_entry_v2(
                db, "CAIXINHA", "DEBIT", Decimal("1.00"), "SEQUENCE", label + "-v2",
                financial_date=date(2026, 9, 30),
            )
            assert first.id is not None and second.id is not None
            assert second.previous_hash == first.entry_hash
            if holder:
                first_ready.set()
                assert release_first.wait(timeout=20)
            db.commit()
            return first.id, second.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(append_pair, "first", holder=True)
        try:
            assert first_ready.wait(timeout=10)
            # Internal writer flushes have not committed the first transaction.
            with Session(sequencing_engine, autoflush=False) as observer:
                assert observer.query(LedgerEntry).count() == 0
            second_future = pool.submit(append_pair, "second", holder=False)
            assert second_started.wait(timeout=10)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                with sequencing_engine.connect() as probe:
                    waiting = probe.execute(sa.text(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks "
                        "WHERE pid = :pid AND locktype = 'advisory' AND NOT granted)"
                    ), {"pid": second_pid[0]}).scalar_one()
                if waiting:
                    break
                time.sleep(0.01)
            else:
                pytest.fail("second writer did not wait for the ledger advisory lock")
            assert not second_future.done()
        finally:
            release_first.set()
        first_ids = first_future.result(timeout=20)
        second_ids = second_future.result(timeout=20)

    with Session(sequencing_engine, autoflush=False) as db:
        rows = db.query(LedgerEntry).order_by(LedgerEntry.id).all()
        assert [row.id for row in rows] == list(first_ids + second_ids)
        assert rows[0].previous_hash is None
        assert all(current.previous_hash == previous.entry_hash for previous, current in zip(rows, rows[1:]))
        assert verify_ledger_chain(db) == {"status": "PASS", "entries": 4, "errors": []}


# The existing PostgreSQL 16 CI step collects the dedicated F2-G3A2 proof.
from test_f2g3a2_postgres_transaction_proof import TestF2G3A2PostgresTransactionProof, proof
