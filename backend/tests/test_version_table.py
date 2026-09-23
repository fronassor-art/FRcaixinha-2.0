import ast
from pathlib import Path
import re

from app.db.alembic_version_table import VERSION_NUM_CAPACITY, prepare_version_table
from sqlalchemy import create_engine, inspect, text


def _version_column(engine):
    column = inspect(engine).get_columns("alembic_version")
    return next(item for item in column if item["name"] == "version_num")


def test_new_sqlite_version_table_has_capacity_and_primary_key():
    engine = create_engine("sqlite:///:memory:")

    with engine.begin() as connection:
        prepare_version_table(connection)
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES ('0012')")
        )

    column = _version_column(engine)
    assert column["type"].length == VERSION_NUM_CAPACITY
    assert inspect(engine).get_pk_constraint("alembic_version")["constrained_columns"] == [
        "version_num"
    ]
    assert engine.connect().execute(text("SELECT version_num FROM alembic_version")).scalar() == "0012"


def test_sqlite_existing_version_table_is_idempotent_and_preserves_data():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL, "
            "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('0011')"))
        prepare_version_table(connection)
        prepare_version_table(connection)

    assert engine.connect().execute(text("SELECT version_num FROM alembic_version")).scalar() == "0011"
    assert inspect(engine).get_pk_constraint("alembic_version")["constrained_columns"] == [
        "version_num"
    ]


def test_sqlite_existing_wider_version_table_is_never_reduced():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(128) NOT NULL, "
            "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
        )
        prepare_version_table(connection)

    assert _version_column(engine)["type"].length == 128


def test_current_revision_ids_and_chain_are_unchanged():
    versions = Path(__file__).parents[1] / "alembic" / "versions"
    rows = {}
    for path in versions.glob("*.py"):
        source = path.read_text()
        revision = re.search(r"(?m)^revision(?:\s*:[^=]+)?\s*=\s*['\"]([^'\"]+)", source)
        down_revision = re.search(r"(?m)^down_revision(?:\s*:[^=]+)?\s*=\s*['\"]([^'\"]+)", source)
        if revision:
            rows[revision.group(1)] = down_revision.group(1) if down_revision else None

    assert len(rows) == 102
    assert max(map(len, rows)) <= VERSION_NUM_CAPACITY
    assert rows["0093_payment_attempt_schema_foundation"] == "0092_versioned_late_charge_foundation"
    assert rows["0094_late_interest_event_contract"] == "0093_payment_attempt_schema_foundation"
    assert rows["0095_pix_attempt_provider_reservation"] == "0094_late_interest_event_contract"
    assert rows["0012_monthly_closing_integrity_v034"] == "0011_penalty_allocation_v029"
    assert rows["0087_monthly_closing_immutability_h3c_a1"] == "0086_payment_settlement_agreement_state_v106"
    assert rows["0088_monthly_closing_snapshot_schema_h3c_b2"] == "0087_monthly_closing_immutability_h3c_a1"
    assert rows["0092_versioned_late_charge_foundation"] == "0091_loan_calculation_version"


def test_drop_constraint_uses_alembic_1614_type_keyword():
    versions = Path(__file__).parents[1] / "alembic" / "versions"
    invalid_calls = []
    for path in versions.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "drop_constraint":
                continue
            if any(keyword.arg == "type" for keyword in node.keywords):
                invalid_calls.append((path.name, node.lineno))

    assert invalid_calls == []
