import importlib.util
from datetime import date
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.models import MonthlyClosing


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0088_monthly_closing_snapshot_schema_h3c_b2.py"
)
spec = importlib.util.spec_from_file_location("migration_0088", MIGRATION_PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def _pre_0088_engine():
    engine = create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table(
        "monthly_closings",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("competence", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("total_contributions", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("total_expenses", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("total_interest_received", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("ledger_balance", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("closed_by", sa.Integer(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("competence", name="uq_monthly_closing_competence"),
    )
    metadata.create_all(engine)
    return engine


def _run(engine, operation):
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            operation()


def test_revision_chain_and_ddl_contract():
    assert migration.revision == "0088_monthly_closing_snapshot_schema_h3c_b2"
    assert migration.down_revision == "0087_monthly_closing_immutability_h3c_a1"
    engine = _pre_0088_engine()
    _run(engine, migration.upgrade)

    columns = {column["name"]: column for column in inspect(engine).get_columns("monthly_closings")}
    assert set(columns) == {column.name for column in MonthlyClosing.__table__.columns}
    assert isinstance(columns["snapshot_json"]["type"], sa.Text)
    assert columns["snapshot_json"]["nullable"] is True
    assert isinstance(columns["snapshot_hash"]["type"], sa.String)
    assert columns["snapshot_hash"]["type"].length == 64
    assert columns["snapshot_hash"]["nullable"] is True
    unique_constraints = inspect(engine).get_unique_constraints("monthly_closings")
    assert {item["name"] for item in unique_constraints} == {
        "uq_monthly_closing_competence",
        "uq_monthly_closing_snapshot_hash",
    }


def test_upgrade_preserves_legacy_closed_null_snapshot_without_dml():
    engine = _pre_0088_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO monthly_closings "
                "(competence, status, total_contributions, total_expenses, "
                "total_interest_received, ledger_balance, created_at) "
                "VALUES (:competence, 'CLOSED', :contributions, :expenses, :interest, :balance, :created_at)"
            ),
            {
                "competence": date(2026, 12, 1),
                "contributions": "100.00",
                "expenses": "5.00",
                "interest": "20.00",
                "balance": "115.00",
                "created_at": date(2026, 12, 1),
            },
        )

    _run(engine, migration.upgrade)
    row = engine.connect().execute(
        text("SELECT status, total_contributions, snapshot_json, snapshot_hash FROM monthly_closings")
    ).one()
    assert row.status == "CLOSED"
    assert str(row.total_contributions) == "100"
    assert row.snapshot_json is None
    assert row.snapshot_hash is None


def test_downgrade_removes_only_snapshot_schema():
    engine = _pre_0088_engine()
    _run(engine, migration.upgrade)
    _run(engine, migration.downgrade)

    columns = {column["name"] for column in inspect(engine).get_columns("monthly_closings")}
    assert "snapshot_json" not in columns
    assert "snapshot_hash" not in columns
    assert {item["name"] for item in inspect(engine).get_unique_constraints("monthly_closings")} == {
        "uq_monthly_closing_competence"
    }
