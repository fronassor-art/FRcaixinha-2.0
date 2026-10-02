"""PostgreSQL 16 contract checks for migration 0107."""

import importlib.util
import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


BACKEND = Path(__file__).resolve().parents[1]
MIGRATION_PATH = BACKEND / "alembic/versions/0107_temporal_receipt_evidence_f2e2.py"


def _safe_url():
    raw = os.environ.get("DATABASE_URL", "")
    try:
        url = sa.engine.make_url(raw)
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


def _migration():
    spec = importlib.util.spec_from_file_location("temporal_receipt_f2e2_pg", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tables(connection):
    columns = """
        id SERIAL PRIMARY KEY,
        receipt_version VARCHAR(20) NOT NULL,
        receipt_number VARCHAR(80),
        receipt_snapshot_json TEXT,
        receipt_hash VARCHAR(64),
        obligation_type VARCHAR(40) NOT NULL,
        financial_date DATE NULL,
        loan_status_before VARCHAR(30), loan_status_after VARCHAR(30),
        loan_state_revision_before INTEGER, loan_state_revision_after INTEGER,
        loan_paid_at_before TIMESTAMPTZ, loan_paid_at_after TIMESTAMPTZ,
        loan_installment_status_before VARCHAR(20), loan_installment_status_after VARCHAR(20),
        loan_installment_paid_at_before TIMESTAMPTZ, loan_installment_paid_at_after TIMESTAMPTZ,
        agreement_installment_status_before VARCHAR(20), agreement_installment_status_after VARCHAR(20),
        agreement_installment_paid_at_before TIMESTAMPTZ, agreement_installment_paid_at_after TIMESTAMPTZ,
        agreement_installment_paid_amount_before NUMERIC, agreement_installment_paid_amount_after NUMERIC,
        agreement_installment_paid_penalty_amount_before NUMERIC, agreement_installment_paid_penalty_amount_after NUMERIC,
        collection_agreement_status_before VARCHAR(20), collection_agreement_status_after VARCHAR(20),
        collection_agreement_state_revision_before INTEGER, collection_agreement_state_revision_after INTEGER,
        principal_applied NUMERIC, penalty_applied NUMERIC
    """
    old = _migration().OLD_SETTLEMENT_CHECKS
    checks = ", ".join(f"CONSTRAINT {name} CHECK ({expr})" for name, expr in old.items())
    connection.exec_driver_sql(f"CREATE TEMP TABLE payment_settlements ({columns}, {checks})")
    connection.exec_driver_sql(
        "CREATE TEMP TABLE payment_reversals (id SERIAL PRIMARY KEY, receipt_version VARCHAR(20) NOT NULL, "
        "receipt_number VARCHAR(80), receipt_snapshot_json TEXT, receipt_hash VARCHAR(64), "
        "financial_date DATE NULL, CONSTRAINT ck_payment_reversals_receipt_version CHECK (receipt_version = 'v1'))"
    )


def _apply(connection, method):
    with Operations.context(MigrationContext.configure(connection)):
        getattr(_migration(), method)()


def test_postgresql_0107_legacy_constraints_and_temporal_downgrade_guard():
    url = _safe_url()
    if url is None:
        pytest.skip("requires isolated PostgreSQL 16 frcaixinha_test CI service")
    engine = sa.create_engine(url)
    try:
        with engine.begin() as connection:
            version_num = int(connection.exec_driver_sql("SHOW server_version_num").scalar_one())
            assert 160000 <= version_num < 170000
            _tables(connection)
            connection.exec_driver_sql(
                "INSERT INTO payment_settlements (receipt_version, receipt_number, receipt_snapshot_json, receipt_hash, obligation_type, principal_applied, penalty_applied) "
                "VALUES ('v1', 'legacy-settlement', '{\"legacy\":true}', 'settlement-hash', 'CONTRIBUTION', 1, 0)"
            )
            connection.exec_driver_sql(
                "INSERT INTO payment_reversals (receipt_version, receipt_number, receipt_snapshot_json, receipt_hash) "
                "VALUES ('v1', 'legacy-reversal', '{\"legacy\":true}', 'reversal-hash')"
            )
            _apply(connection, "upgrade")
            assert connection.exec_driver_sql("SELECT financial_date FROM payment_settlements").scalar_one() is None
            assert connection.exec_driver_sql("SELECT financial_date FROM payment_reversals").scalar_one() is None
            assert connection.exec_driver_sql(
                "SELECT receipt_number, receipt_snapshot_json, receipt_hash FROM payment_settlements"
            ).one() == ("legacy-settlement", '{"legacy":true}', "settlement-hash")
            assert connection.exec_driver_sql(
                "SELECT receipt_number, receipt_snapshot_json, receipt_hash FROM payment_reversals"
            ).one() == ("legacy-reversal", '{"legacy":true}', "reversal-hash")
            assert connection.exec_driver_sql(
                "SELECT pg_typeof(financial_date)::text FROM payment_settlements"
            ).scalar_one() == "date"

            connection.exec_driver_sql(
                "INSERT INTO payment_settlements (receipt_version, obligation_type, financial_date, principal_applied, penalty_applied) "
                "VALUES ('v6', 'CONTRIBUTION', DATE '2026-09-30', 1, 0)"
            )
            with pytest.raises(sa.exc.IntegrityError):
                with connection.begin_nested():
                    connection.exec_driver_sql(
                        "INSERT INTO payment_settlements (receipt_version, obligation_type, financial_date) "
                        "VALUES ('v1', 'CONTRIBUTION', DATE '2026-09-30')"
                    )
            with pytest.raises(sa.exc.IntegrityError):
                with connection.begin_nested():
                    connection.exec_driver_sql(
                        "INSERT INTO payment_settlements (receipt_version, obligation_type, financial_date) "
                        "VALUES ('v6', 'CONTRIBUTION', NULL)"
                    )
            with pytest.raises(sa.exc.IntegrityError):
                with connection.begin_nested():
                    connection.exec_driver_sql(
                        "UPDATE payment_reversals SET financial_date=DATE '2026-09-30' WHERE id=1"
                    )
            with pytest.raises(sa.exc.IntegrityError):
                with connection.begin_nested():
                    connection.exec_driver_sql(
                        "INSERT INTO payment_reversals (receipt_version, financial_date) VALUES ('v2', NULL)"
                    )
            connection.exec_driver_sql(
                "INSERT INTO payment_reversals (receipt_version, financial_date) VALUES ('v2', DATE '2026-09-30')"
            )
            with pytest.raises(RuntimeError, match="contains temporal evidence"):
                _apply(connection, "downgrade")
            connection.exec_driver_sql("DELETE FROM payment_settlements WHERE receipt_version = 'v6'")
            with pytest.raises(RuntimeError, match="contains temporal evidence"):
                _apply(connection, "downgrade")
            connection.exec_driver_sql("DELETE FROM payment_reversals")
            connection.exec_driver_sql("DELETE FROM payment_settlements")
            _apply(connection, "downgrade")
            _apply(connection, "upgrade")
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM information_schema.columns WHERE table_name='payment_settlements' AND column_name='financial_date'"
            ).scalar_one() == 1
    finally:
        engine.dispose()
