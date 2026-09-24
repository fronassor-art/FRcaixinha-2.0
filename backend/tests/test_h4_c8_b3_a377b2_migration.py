"""0098 chain, SQLite roundtrip and optional PostgreSQL roundtrip tests."""
import os
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import text

ROOT = Path(__file__).parents[1]
REVISION = "0098_cycle_closing_persistence_a377b2"
PARENT = "0097_cycle_participation_a377b1"


def _alembic_config(url):
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return cfg


def _insert_legacy_loan(connection):
    now = datetime(2027, 1, 1, tzinfo=timezone.utc).isoformat()
    connection.execute(text(
        "INSERT INTO users (id,name,email,cpf,password_hash,role,is_active,is_master,created_at) "
        "VALUES (1,:name,:email,:cpf,:password,:role,TRUE,FALSE,:now)"
    ), {"name": "legacy", "email": "legacy@example.test", "cpf": "00000000000001",
        "password": "unused", "role": "USER", "now": now})
    connection.execute(text(
        "INSERT INTO groups (id,name,monthly_amount,months,due_day,active,min_cash_reserve,"
        "max_simultaneous_loans,max_installments,grace_days,max_overdue_installments) "
        "VALUES (1,:name,150,12,10,TRUE,0,1,6,0,0)"
    ), {"name": "legacy-group"})
    connection.execute(text(
        "INSERT INTO members (id,user_id,group_id,status,joined_at) VALUES (1,1,1,:status,:now)"
    ), {"status": "ACTIVE", "now": now})
    cycle_id = connection.execute(text("SELECT id FROM cycles ORDER BY id LIMIT 1")).scalar_one()
    connection.execute(text(
        "INSERT INTO cycle_participations (id,cycle_id,member_id,status) VALUES (1,:cycle,1,:status)"
    ), {"cycle": cycle_id, "status": "ACTIVE"})
    connection.execute(text(
        "INSERT INTO loans (id,member_id,principal,principal_settled_with_own_balance,monthly_rate,installments,status,requested_at,state_revision) "
        "VALUES (1,1,100,0,0.2,1,:status,:now,0)"
    ), {"status": "ACTIVE", "now": now})


def _insert_and_check_immutable_guards(connection):
    now = datetime(2027, 12, 9, 12, tzinfo=timezone.utc).isoformat()
    connection.execute(text(
        "INSERT INTO cycle_annual_closings (cycle_id,status,state_revision) VALUES (1,:status,0)"
    ), {"status": "MASTER_APPROVED"})
    closing_id = connection.execute(text("SELECT id FROM cycle_annual_closings WHERE cycle_id=1")).scalar_one()
    connection.execute(text(
        "INSERT INTO cycle_annual_closing_snapshots "
        "(closing_id,cycle_id,snapshot_version,closing_cutoff_at,calculation_version,canonical_payload,payload_hash,"
        "gross_realized_result,administration_fee_rate,administration_fee,distributable_result,total_eligible_contributions) "
        "VALUES (:closing,1,:snapshot,:cutoff,:calc,:payload,:hash,0,0.15,0,0,0)"
    ), {"closing": closing_id, "snapshot": "cycle_annual_closing_snapshot_v1", "cutoff": now,
        "calc": "cycle_closing_v1", "payload": "{}", "hash": "a" * 64})
    original_id = connection.execute(text(
        "INSERT INTO cycle_realized_gain_events "
        "(cycle_id,event_type,amount,realized_at,source_type,source_id,idempotency_key,evidence_reference,evidence_hash) "
        "VALUES (1,:type,5,:when,:source,:source_id,:key,:reference,:hash) RETURNING id"
    ), {"type": "OTHER_REALIZED_GAIN", "when": now, "source": "BANK_STATEMENT",
        "source_id": "entry-1", "key": "gain-1", "reference": "statement#1", "hash": "b" * 64}).scalar_one()
    connection.execute(text(
        "INSERT INTO cycle_realized_gain_events "
        "(cycle_id,event_type,amount,realized_at,source_type,source_id,idempotency_key,evidence_reference,evidence_hash,reversal_of_id) "
        "VALUES (1,:type,5,:when,:source,:source_id,:key,:reference,:hash,:original)"
    ), {"type": "OTHER_REALIZED_GAIN", "when": now, "source": "BANK_CORRECTION",
        "source_id": "entry-1-reversal", "key": "gain-reversal-1", "reference": "reversal#1",
        "hash": "c" * 64, "original": original_id})
    for source_type in (
        "PAYMENT", "SETTLEMENT", "LOAN_INTEREST", "ARBITRARY", "BANK_STATEMENT_FAKE",
    ):
        with pytest.raises(sa.exc.DatabaseError):
            with connection.begin_nested():
                connection.execute(text(
                    "INSERT INTO cycle_realized_gain_events "
                    "(cycle_id,event_type,amount,realized_at,source_type,source_id,idempotency_key,evidence_reference,evidence_hash) "
                    "VALUES (1,'OTHER_REALIZED_GAIN',1,:when,:source,:source_id,:key,:reference,:hash)"
                ), {"when": now, "source": source_type, "source_id": f"direct-{source_type}",
                    "key": f"direct-{source_type}", "reference": "direct-test",
                    "hash": ("e" * 64)})
    with pytest.raises(sa.exc.DatabaseError):
        with connection.begin_nested():
            connection.execute(text(
                "INSERT INTO cycle_realized_gain_events "
                "(cycle_id,event_type,amount,realized_at,source_type,source_id,idempotency_key,evidence_reference,evidence_hash,reversal_of_id) "
                "VALUES (1,:type,4,:when,:source,:source_id,:key,:reference,:hash,:original)"
            ), {"type": "OTHER_REALIZED_GAIN", "when": now, "source": "BANK_CORRECTION",
                "source_id": "entry-1-invalid-reversal", "key": "gain-reversal-invalid",
                "reference": "reversal-invalid#1", "hash": "d" * 64, "original": original_id})
    for sql, params in (
        ("UPDATE cycle_annual_closing_snapshots SET payload_hash=:hash WHERE cycle_id=1", {"hash": "c" * 64}),
        ("DELETE FROM cycle_annual_closing_snapshots WHERE cycle_id=1", {}),
        ("UPDATE cycle_realized_gain_events SET amount=6 WHERE cycle_id=1", {}),
        ("DELETE FROM cycle_realized_gain_events WHERE cycle_id=1", {}),
    ):
        with pytest.raises(sa.exc.DatabaseError):
            with connection.begin_nested():
                connection.execute(text(sql), params)


def _run_roundtrip(url, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "database_url", url)
    cfg = _alembic_config(url)
    command.upgrade(cfg, PARENT)
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == PARENT
        before = sa.inspect(connection)
        assert "cycle_annual_closings" not in before.get_table_names()
        assert "cycle_id" not in {col["name"] for col in before.get_columns("loans")}
        old_loan_indexes = {item["name"] for item in before.get_indexes("loans")}
        _insert_legacy_loan(connection)
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == REVISION
        inspector = sa.inspect(connection)
        assert {"cycle_annual_closings", "cycle_annual_closing_snapshots", "cycle_realized_gain_events"} <= set(inspector.get_table_names())
        loan_columns = {col["name"]: col for col in inspector.get_columns("loans")}
        assert loan_columns["cycle_id"]["nullable"] is True
        assert old_loan_indexes <= {item["name"] for item in inspector.get_indexes("loans")}
        assert "ix_loans_cycle_id" in {item["name"] for item in inspector.get_indexes("loans")}
        loan_fks = inspector.get_foreign_keys("loans")
        assert any(fk["constrained_columns"] == ["member_id", "cycle_id"]
                   and fk["referred_table"] == "cycle_participations"
                   and fk["referred_columns"] == ["member_id", "cycle_id"] for fk in loan_fks)
        assert connection.execute(text("SELECT count(*) FROM loans")).scalar_one() == 1
        assert connection.execute(text("SELECT cycle_id FROM loans WHERE id=1")).scalar_one() is None
        if connection.dialect.name == "sqlite":
            trigger_names = {row[0] for row in connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ))}
            assert {"trg_cycle_annual_snapshots_no_update", "trg_cycle_annual_snapshots_no_delete",
                    "trg_cycle_gain_events_no_update", "trg_cycle_gain_events_no_delete",
                    "trg_cycle_gain_events_full_reversal"} <= trigger_names
        _insert_and_check_immutable_guards(connection)
    engine.dispose()

    command.downgrade(cfg, PARENT)
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == PARENT
        assert "cycle_annual_closings" not in sa.inspect(connection).get_table_names()
        assert "cycle_id" not in {col["name"] for col in sa.inspect(connection).get_columns("loans")}
        assert connection.execute(text("SELECT count(*) FROM loans WHERE id=1")).scalar_one() == 1
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = sa.create_engine(url)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == REVISION
        assert connection.execute(text("SELECT cycle_id FROM loans WHERE id=1")).scalar_one() is None
    engine.dispose()


def test_0098_sqlite_upgrade_downgrade_reupgrade(tmp_path, monkeypatch):
    _run_roundtrip(f"sqlite:///{tmp_path / 'a377b2_roundtrip.db'}", monkeypatch)


def test_0098_postgresql_upgrade_downgrade_reupgrade_if_configured(monkeypatch):
    raw_url = os.environ.get("A377B1_POSTGRES_TEST_URL")
    if not raw_url:
        pytest.skip("A377B1_POSTGRES_TEST_URL is not configured")
    base_url = sa.engine.make_url(raw_url)
    if base_url.get_backend_name() != "postgresql" or "a377b1_test" not in (base_url.database or "").lower():
        pytest.fail("PostgreSQL URL must target a dedicated a377b1_test database")
    schema = "a377b2_" + uuid4().hex[:12]
    admin_engine = sa.create_engine(raw_url, pool_pre_ping=True)
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        isolated_url = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
        _run_roundtrip(isolated_url.render_as_string(hide_password=False), monkeypatch)
    finally:
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
