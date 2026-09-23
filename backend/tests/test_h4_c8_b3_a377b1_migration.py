"""A3.77B1 additive migration and rollback proof."""
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "m0097", ROOT / "alembic/versions/0097_cycle_participation_a377b1.py"
)
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


def old_schema(connection):
    metadata = sa.MetaData()
    sa.Table(
        "cycles", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
    )
    sa.Table(
        "members", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
    )
    sa.Table(
        "quotas", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("member_id", sa.Integer, nullable=False),
        sa.Column("cycle_id", sa.Integer),
    )
    sa.Table(
        "contributions", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("member_id", sa.Integer, nullable=False),
        sa.Column("cycle_id", sa.Integer),
    )
    metadata.create_all(connection)
    connection.execute(sa.text("INSERT INTO cycles (id) VALUES (1)"))
    connection.execute(sa.text("INSERT INTO members (id) VALUES (1)"))
    connection.execute(sa.text("INSERT INTO quotas (id, member_id, cycle_id) VALUES (1, 1, 1)"))
    connection.execute(sa.text("INSERT INTO contributions (id, member_id, cycle_id) VALUES (1, 1, 1)"))


def apply(connection, action):
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        action()


def verify_round_trip(connection):
    old_schema(connection)
    apply(connection, migration.upgrade)
    inspector = sa.inspect(connection)
    assert "cycle_participations" in inspector.get_table_names()
    assert "contribution_charge_events" in inspector.get_table_names()
    columns = {item["name"] for item in inspector.get_columns("contributions")}
    assert {"cancelled_at", "cancellation_reason"} <= columns
    row = connection.execute(sa.text(
        "SELECT member_id, cycle_id, status, joined_at FROM cycle_participations"
    )).one()
    assert row == (1, 1, "ACTIVE", None)
    apply(connection, migration.downgrade)
    assert "cycle_participations" not in sa.inspect(connection).get_table_names()
    assert "contribution_charge_events" not in sa.inspect(connection).get_table_names()
    assert connection.execute(sa.text("SELECT COUNT(*) FROM quotas")).scalar_one() == 1
    assert connection.execute(sa.text("SELECT COUNT(*) FROM contributions")).scalar_one() == 1
    apply(connection, migration.upgrade)
    assert connection.execute(sa.text("SELECT COUNT(*) FROM cycle_participations")).scalar_one() == 1


def test_revision_and_sqlite_upgrade_downgrade_upgrade():
    assert migration.revision == "0097_cycle_participation_a377b1"
    assert migration.down_revision == "0096_cycle_foundation_a377a"
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        verify_round_trip(connection)
    engine.dispose()


def test_downgrade_refuses_transition_data():
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        old_schema(connection)
        apply(connection, migration.upgrade)
        connection.execute(sa.text(
            "UPDATE cycle_participations SET status='BLOCKED_DELINQUENCY', "
            "blocked_at='2027-04-11 12:00:00', block_reason='THREE_CONSECUTIVE_OVERDUE'"
        ))
        with pytest.raises(RuntimeError, match="refusing destructive downgrade"):
            apply(connection, migration.downgrade)
    engine.dispose()


def test_downgrade_refuses_new_active_participation():
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        old_schema(connection)
        apply(connection, migration.upgrade)
        connection.execute(sa.text(
            "UPDATE cycle_participations SET joined_at='2027-01-10 03:00:00'"
        ))
        with pytest.raises(RuntimeError, match="refusing destructive downgrade"):
            apply(connection, migration.downgrade)
    engine.dispose()


def test_postgresql_upgrade_downgrade_upgrade_if_configured():
    raw_url = os.environ.get("A377B1_POSTGRES_TEST_URL")
    if not raw_url:
        pytest.skip("A377B1_POSTGRES_TEST_URL is not configured")
    url = sa.engine.make_url(raw_url)
    if url.get_backend_name() != "postgresql" or "a377b1_test" not in (url.database or "").lower():
        pytest.fail("PostgreSQL URL must target a dedicated a377b1_test database")
    schema = "a377b1_" + uuid4().hex[:12]
    engine = sa.create_engine(raw_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        with engine.begin() as connection:
            connection.execute(sa.text(f'SET search_path TO "{schema}"'))
            verify_round_trip(connection)
    finally:
        with engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
