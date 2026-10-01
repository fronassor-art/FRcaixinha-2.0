"""PostgreSQL DDL safety for the F2-E1 event date columns."""

import importlib.util
import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    BACKEND_ROOT / "alembic" / "versions" / "0106_event_financial_date_f2e1.py"
)


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


def test_postgresql_upgrade_preserves_legacy_and_downgrade_fails_closed():
    url = _safe_test_url()
    if url is None:
        pytest.skip("requires isolated PostgreSQL 16 frcaixinha_test CI service")

    spec = importlib.util.spec_from_file_location("event_financial_date_f2e1_pg", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TEMP TABLE payment_settlements (id SERIAL PRIMARY KEY, "
                "receipt_version VARCHAR(20) NOT NULL, receipt_snapshot_json TEXT NOT NULL, "
                "receipt_hash VARCHAR(64) NOT NULL, confirmed_at TIMESTAMPTZ NOT NULL)"
            )
            connection.exec_driver_sql(
                "CREATE TEMP TABLE payment_reversals (id SERIAL PRIMARY KEY, "
                "receipt_version VARCHAR(20) NOT NULL, receipt_snapshot_json TEXT NOT NULL, "
                "receipt_hash VARCHAR(64) NOT NULL, reversed_at TIMESTAMPTZ NOT NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO payment_settlements "
                "(receipt_version, receipt_snapshot_json, receipt_hash, confirmed_at) "
                "VALUES ('v1', '{\"legacy\":true}', 'settlement-hash', '2026-09-30T23:55:00Z')"
            )
            connection.exec_driver_sql(
                "INSERT INTO payment_reversals "
                "(receipt_version, receipt_snapshot_json, receipt_hash, reversed_at) "
                "VALUES ('v1', '{\"legacy\":true}', 'reversal-hash', '2026-10-01T00:05:00Z')"
            )

            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

            for table in ("payment_settlements", "payment_reversals"):
                row = connection.exec_driver_sql(
                    f"SELECT receipt_version, receipt_snapshot_json, receipt_hash, financial_date, "
                    f"pg_typeof(financial_date)::text "
                    f"FROM {table} WHERE id = 1"
                ).one()
                assert row[0] == "v1"
                assert row[1] == '{"legacy":true}'
                assert row[3] is None
                assert row[4] == "date"
                nullable = connection.exec_driver_sql(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_schema = pg_my_temp_schema() AND table_name = %s "
                    "AND column_name = 'financial_date'",
                    (table,),
                ).scalar_one()
                assert nullable == "YES"

            connection.exec_driver_sql(
                "UPDATE payment_settlements SET financial_date = DATE '2026-09-30' WHERE id = 1"
            )
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(RuntimeError, match="payment_settlements contains financial-date values"):
                    migration.downgrade()
            connection.exec_driver_sql("UPDATE payment_settlements SET financial_date = NULL WHERE id = 1")
            connection.exec_driver_sql(
                "UPDATE payment_reversals SET financial_date = DATE '2026-10-01' WHERE id = 1"
            )
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(RuntimeError, match="payment_reversals contains financial-date values"):
                    migration.downgrade()

            connection.exec_driver_sql("UPDATE payment_reversals SET financial_date = NULL WHERE id = 1")
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                migration.upgrade()
            for table in ("payment_settlements", "payment_reversals"):
                column_exists = connection.exec_driver_sql(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = pg_my_temp_schema() AND table_name = %s "
                    "AND column_name = 'financial_date'",
                    (table,),
                ).scalar_one_or_none()
                assert column_exists == 1
    finally:
        engine.dispose()
