"""SQLite checks for the temporal receipt version/date transition."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.config import Config
from alembic.script import ScriptDirectory


BACKEND = Path(__file__).resolve().parents[1]
MIGRATION_PATH = BACKEND / "alembic/versions/0107_temporal_receipt_evidence_f2e2.py"


def _migration():
    spec = importlib.util.spec_from_file_location("temporal_receipt_f2e2", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schema(connection):
    settlement_checks = _migration().OLD_SETTLEMENT_CHECKS
    metadata = sa.MetaData()
    settlement_columns = [
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("receipt_version", sa.String(20), nullable=False),
        sa.Column("receipt_number", sa.String(80)),
        sa.Column("receipt_snapshot_json", sa.Text),
        sa.Column("receipt_hash", sa.String(64)),
        sa.Column("obligation_type", sa.String(40), nullable=False),
        sa.Column("financial_date", sa.Date, nullable=True),
        sa.Column("loan_status_before", sa.String(30)),
        sa.Column("loan_status_after", sa.String(30)),
        sa.Column("loan_state_revision_before", sa.Integer),
        sa.Column("loan_state_revision_after", sa.Integer),
        sa.Column("loan_paid_at_before", sa.DateTime),
        sa.Column("loan_paid_at_after", sa.DateTime),
        sa.Column("loan_installment_status_before", sa.String(20)),
        sa.Column("loan_installment_status_after", sa.String(20)),
        sa.Column("loan_installment_paid_at_before", sa.DateTime),
        sa.Column("loan_installment_paid_at_after", sa.DateTime),
        sa.Column("agreement_installment_status_before", sa.String(20)),
        sa.Column("agreement_installment_status_after", sa.String(20)),
        sa.Column("agreement_installment_paid_at_before", sa.DateTime),
        sa.Column("agreement_installment_paid_at_after", sa.DateTime),
        sa.Column("agreement_installment_paid_amount_before", sa.Numeric),
        sa.Column("agreement_installment_paid_amount_after", sa.Numeric),
        sa.Column("agreement_installment_paid_penalty_amount_before", sa.Numeric),
        sa.Column("agreement_installment_paid_penalty_amount_after", sa.Numeric),
        sa.Column("collection_agreement_status_before", sa.String(20)),
        sa.Column("collection_agreement_status_after", sa.String(20)),
        sa.Column("collection_agreement_state_revision_before", sa.Integer),
        sa.Column("collection_agreement_state_revision_after", sa.Integer),
        sa.Column("principal_applied", sa.Numeric),
        sa.Column("penalty_applied", sa.Numeric),
    ]
    settlement_columns.extend(
        sa.CheckConstraint(expression, name=name)
        for name, expression in settlement_checks.items()
    )
    sa.Table("payment_settlements", metadata, *settlement_columns).create(connection)
    assert {item["name"] for item in sa.inspect(connection).get_check_constraints("payment_settlements")} == set(settlement_checks)
    reversal = sa.Table(
        "payment_reversals",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("receipt_version", sa.String(20), nullable=False),
        sa.Column("receipt_number", sa.String(80)),
        sa.Column("receipt_snapshot_json", sa.Text),
        sa.Column("receipt_hash", sa.String(64)),
        sa.Column("financial_date", sa.Date, nullable=True),
        sa.CheckConstraint("receipt_version = 'v1'", name="ck_payment_reversals_receipt_version"),
    )
    reversal.create(connection)


def _apply(connection, name):
    with Operations.context(MigrationContext.configure(connection)):
        getattr(_migration(), name)()


def _insert_legacy(connection):
    connection.exec_driver_sql(
        "INSERT INTO payment_settlements (id, receipt_version, receipt_number, receipt_snapshot_json, receipt_hash, obligation_type, principal_applied, penalty_applied) "
        "VALUES (1, 'v1', 'legacy-settlement', '{\"legacy\":true}', 'settlement-hash', 'CONTRIBUTION', 0, 0)"
    )
    connection.exec_driver_sql(
        "INSERT INTO payment_reversals (id, receipt_version, receipt_number, receipt_snapshot_json, receipt_hash) "
        "VALUES (1, 'v1', 'legacy-reversal', '{\"legacy\":true}', 'reversal-hash')"
    )


def test_f2e2_migration_chain_and_single_head():
    scripts = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    assert scripts.get_revision("0107_temporal_receipt_evidence_f2e2").down_revision == "0106_event_financial_date_f2e1"
    assert scripts.get_current_head() == "0107_temporal_receipt_evidence_f2e2"


def test_upgrade_legacy_constraints_no_backfill_and_round_trip():
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _schema(connection)
        _insert_legacy(connection)
        _apply(connection, "upgrade")
        for table in ("payment_settlements", "payment_reversals"):
            financial_date = next(
                column for column in sa.inspect(connection).get_columns(table)
                if column["name"] == "financial_date"
            )
            assert financial_date["nullable"] is True
            assert financial_date["default"] is None
            assert not any(
                "financial_date" in index["column_names"]
                for index in sa.inspect(connection).get_indexes(table)
            )
        assert connection.exec_driver_sql(
            "SELECT receipt_number, receipt_snapshot_json, receipt_hash FROM payment_settlements WHERE id=1"
        ).one() == ("legacy-settlement", '{"legacy":true}', "settlement-hash")
        assert connection.exec_driver_sql(
            "SELECT receipt_number, receipt_snapshot_json, receipt_hash FROM payment_reversals WHERE id=1"
        ).one() == ("legacy-reversal", '{"legacy":true}', "reversal-hash")
        assert connection.exec_driver_sql("SELECT financial_date FROM payment_settlements WHERE id=1").scalar_one() is None
        assert connection.exec_driver_sql("SELECT financial_date FROM payment_reversals WHERE id=1").scalar_one() is None
        _apply(connection, "downgrade")
        assert "financial_date" in {column["name"] for column in sa.inspect(connection).get_columns("payment_settlements")}
        _apply(connection, "upgrade")
    engine.dispose()


@pytest.mark.parametrize("version,date_value", [("v6", None), ("v1", "2026-09-30")])
def test_settlement_version_date_pairs_fail_closed(version, date_value):
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _schema(connection)
        _apply(connection, "upgrade")
        with connection.begin_nested():
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "INSERT INTO payment_settlements (id, receipt_version, obligation_type, financial_date, principal_applied, penalty_applied) "
                    "VALUES (1, ?, 'CONTRIBUTION', ?, 1, 0)",
                    (version, date_value),
                )
    engine.dispose()


@pytest.mark.parametrize("version,date_value", [("v2", None), ("v1", "2026-09-30")])
def test_reversal_version_date_pairs_fail_closed(version, date_value):
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _schema(connection)
        _apply(connection, "upgrade")
        with connection.begin_nested():
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "INSERT INTO payment_reversals (id, receipt_version, financial_date) VALUES (1, ?, ?)",
                    (version, date_value),
                )
    engine.dispose()


def test_v6_contribution_with_financial_date_is_allowed_by_schema():
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _schema(connection)
        _apply(connection, "upgrade")
        connection.exec_driver_sql(
            "INSERT INTO payment_settlements (id, receipt_version, obligation_type, financial_date, principal_applied, penalty_applied) "
            "VALUES (1, 'v6', 'CONTRIBUTION', '2026-09-30', 1, 0)"
        )
        assert connection.exec_driver_sql("SELECT financial_date FROM payment_settlements WHERE id=1").scalar_one() == "2026-09-30"
    engine.dispose()


@pytest.mark.parametrize("table,version,date_value", [
    ("payment_settlements", "v6", "2026-09-30"),
    ("payment_reversals", "v2", "2026-09-30"),
    ("payment_settlements", "v1", "2026-09-30"),
])
def test_downgrade_refuses_every_temporal_or_dated_row(table, version, date_value):
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _schema(connection)
        _insert_legacy(connection)
        _apply(connection, "upgrade")
        if table == "payment_settlements":
            if version == "v1" and date_value is not None:
                # Simulate a pre-existing/corrupt row that bypassed CHECK
                # enforcement; downgrade must still refuse to drop its date.
                connection.exec_driver_sql("PRAGMA ignore_check_constraints = ON")
            connection.exec_driver_sql(
                "UPDATE payment_settlements SET receipt_version=?, financial_date=? WHERE id=1",
                (version, date_value),
            )
        else:
            connection.exec_driver_sql(
                "UPDATE payment_reversals SET receipt_version=?, financial_date=? WHERE id=1",
                (version, date_value),
            )
        with pytest.raises(RuntimeError, match="contains temporal evidence"):
            _apply(connection, "downgrade")
    engine.dispose()
