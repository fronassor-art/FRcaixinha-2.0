"""0099 structural roundtrip and database enforced review immutability."""

import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import text


ROOT = Path(__file__).parents[1]
PARENT = "0098_cycle_closing_persistence_a377b2"
REVISION = "0099_cycle_closing_review_a377b3r1"


def config():
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return cfg


def _run_migration(action, cfg, url, revision):
    action(cfg, revision)


def roundtrip(url):
    fake = ModuleType("app.core.config")
    fake.settings = SimpleNamespace(database_url=url)
    previous = sys.modules.get("app.core.config")
    sys.modules["app.core.config"] = fake
    try:
        _roundtrip(url)
    finally:
        if previous is None:
            sys.modules.pop("app.core.config", None)
        else:
            sys.modules["app.core.config"] = previous


def _roundtrip(url):
    cfg = config()
    _run_migration(command.upgrade, cfg, url, PARENT)
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO users (id,name,email,cpf,password_hash,role,is_active,is_master,created_at) "
            "VALUES (1,'reviewer','reviewer@example.test','00000000000001','unused','ADMIN',TRUE,TRUE,'2026-09-24T00:00:00+00:00')"
        ))
        assert connection.execute(text("SELECT count(*) FROM cycles WHERE id=1")).scalar_one() == 1
        connection.execute(text("INSERT INTO cycle_annual_closings (id,cycle_id,status,state_revision) VALUES (1,1,'ASSESSING',0)"))
        connection.execute(text(
            "INSERT INTO cycle_annual_closing_snapshots "
            "(closing_id,cycle_id,snapshot_version,closing_cutoff_at,calculation_version,canonical_payload,payload_hash,"
            "gross_realized_result,administration_fee_rate,administration_fee,distributable_result,total_eligible_contributions) "
            "VALUES (1,1,'cycle_annual_closing_snapshot_v1','2027-12-10T18:00:00+00:00',"
            "'cycle_closing_v1','{}',:hash,0,0.15,0,0,0)"
        ), {"hash": "b" * 64})
        connection.execute(text(
            "INSERT INTO cycle_realized_gain_events "
            "(cycle_id,event_type,amount,realized_at,source_type,source_id,idempotency_key,evidence_reference,evidence_hash) "
            "VALUES (1,'OTHER_REALIZED_GAIN',1,'2027-12-09T18:00:00+00:00','BANK_STATEMENT',"
            "'bank-row-1','gain-1','bank/statement#1',:hash)"
        ), {"hash": "c" * 64})
    engine.dispose()

    _run_migration(command.upgrade, cfg, url, REVISION)
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        inspector = sa.inspect(connection)
        assert "cycle_annual_closing_reviews" in inspector.get_table_names()
        assert "approved_review_id" in {col["name"] for col in inspector.get_columns("cycle_annual_closings")}
        closing_fks = inspector.get_foreign_keys("cycle_annual_closings")
        approved_review_fk = next(
            fk for fk in closing_fks
            if fk["constrained_columns"] == ["approved_review_id"]
        )
        assert approved_review_fk["referred_table"] == "cycle_annual_closing_reviews"
        assert approved_review_fk["referred_columns"] == ["id"]
        assert any(
            fk["constrained_columns"] == ["cycle_id"]
            and fk["referred_table"] == "cycles"
            for fk in closing_fks
        )
        assert any(
            fk["constrained_columns"] == ["closing_id", "cycle_id"]
            and fk["referred_table"] == "cycle_annual_closings"
            and fk["referred_columns"] == ["id", "cycle_id"]
            for fk in inspector.get_foreign_keys("cycle_annual_closing_snapshots")
        )
        assert any(
            fk["constrained_columns"] == ["closing_id", "cycle_id"]
            and fk["referred_table"] == "cycle_annual_closings"
            and fk["referred_columns"] == ["id", "cycle_id"]
            for fk in inspector.get_foreign_keys("cycle_annual_closing_cash_evidence")
        )
        cash_fks = inspector.get_foreign_keys("cycle_annual_closing_cash_evidence")
        assert any(fk["constrained_columns"] == ["file_id"] and fk["referred_table"] == "workflow_execution_evidence_files" for fk in cash_fks)
        assert any(fk["constrained_columns"] == ["uploaded_by"] and fk["referred_table"] == "users" for fk in cash_fks)
        assert any(fk["constrained_columns"] == ["attested_by"] and fk["referred_table"] == "users" for fk in cash_fks)
        assert "ix_cycle_annual_closings_status" in {
            index["name"] for index in inspector.get_indexes("cycle_annual_closings")
        }
        assert any(
            fk["constrained_columns"] == ["cycle_id"]
            and fk["referred_table"] == "cycles"
            for fk in inspector.get_foreign_keys("cycle_annual_closings")
        )
        assert any(
            fk["constrained_columns"] == ["closing_id", "cycle_id"]
            and fk["referred_table"] == "cycle_annual_closings"
            and fk["referred_columns"] == ["id", "cycle_id"]
            for fk in inspector.get_foreign_keys("cycle_annual_closing_snapshots")
        )
        assert connection.execute(text("SELECT count(*) FROM cycle_annual_closings WHERE id=1")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM cycle_annual_closing_snapshots")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM cycle_realized_gain_events")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM cycle_annual_closing_reviews")).scalar_one() == 0
        connection.execute(text(
            "INSERT INTO operational_workflow_tasks "
            "(id,action_code,status,priority,created_by,created_at,updated_at,sla_status,escalation_level) "
            "VALUES (1,'CASH_TEST','OPEN','MEDIUM',1,'2027-12-10T18:00:00+00:00',"
            "'2027-12-10T18:00:00+00:00','ON_TRACK','NONE')"
        ))
        connection.execute(text(
            "INSERT INTO workflow_execution_evidence "
            "(id,task_id,added_by,evidence_type,title,content,content_hash,created_at) "
            "VALUES (1,1,1,'ATTACHMENT','cash','stored evidence',:hash,'2027-12-10T18:00:00+00:00')"
        ), {"hash": "d" * 64})
        connection.execute(text(
            "INSERT INTO workflow_execution_evidence_files "
            "(id,evidence_id,version,original_name,storage_key,content_type,size_bytes,sha256,uploaded_by,created_at) "
            "VALUES (1,1,1,'cash.txt','cash-test.txt','text/plain',1,:hash,1,'2027-12-10T18:00:00+00:00')"
        ), {"hash": "e" * 64})
        connection.execute(text(
            "INSERT INTO cycle_annual_closing_cash_evidence "
            "(id,closing_id,cycle_id,file_id,storage_reference,file_sha256,declared_cash_balance,"
            "observed_at,closing_cutoff_at,uploaded_by,attested_by,attested_at) "
            "VALUES (1,1,1,1,'cash-test.txt',:hash,0,'2027-12-10T18:00:00+00:00',"
            "'2027-12-10T18:00:00+00:00',1,1,'2027-12-10T18:00:00+00:00')"
        ), {"hash": "e" * 64})
        connection.execute(text(
            "INSERT INTO cycle_annual_closing_reviews (closing_id,cycle_id,review_version,process_revision,"
            "cash_evidence_id,closing_cutoff_at,calculation_version,calculation_hash,ledger_cash_balance,actual_cash_balance,"
            "reconciliation_difference,participant_payout_liability,administration_fee,required_liquidity,"
            "liquidity_surplus,cash_evidence_reference,cash_evidence_hash,reconciliation_payload,"
            "reconciliation_hash,review_hash,created_by) VALUES "
            "(1,1,1,1,1,'2027-12-10T18:00:00+00:00','cycle_closing_v1',:hash,0,0,0,0,0,0,0,"
            "'cash#1',:hash,'{}',:hash,:hash,1)"
        ), {"hash": "a" * 64})
        review_id = connection.execute(text("SELECT id FROM cycle_annual_closing_reviews")).scalar_one()
        with pytest.raises(sa.exc.DatabaseError):
            with connection.begin_nested():
                connection.execute(text("UPDATE cycle_annual_closings SET approved_review_id=999 WHERE id=1"))

        # Create a valid review for a different closing/cycle. Its review FK is
        # valid, but the additional ownership guard must reject linking it.
        connection.execute(text(
            "INSERT INTO cycles (id,start_date,entry_deadline,closing_reference_date,monthly_amount,months,max_quotas,status,created_at) "
            "VALUES (2,'2027-12-11','2028-01-10','2028-12-11',150,12,50,'OPEN','2026-09-24T00:00:00+00:00')"
        ))
        connection.execute(text(
            "INSERT INTO cycle_annual_closings (id,cycle_id,status,state_revision) "
            "VALUES (2,2,'ASSESSING',0)"
        ))
        connection.execute(text(
            "INSERT INTO cycle_annual_closing_cash_evidence "
            "(id,closing_id,cycle_id,file_id,storage_reference,file_sha256,declared_cash_balance,"
            "observed_at,closing_cutoff_at,uploaded_by,attested_by,attested_at) "
            "VALUES (2,2,2,1,'cash-test-2.txt',:hash,0,'2027-12-10T18:00:00+00:00',"
            "'2027-12-10T18:00:00+00:00',1,1,'2027-12-10T18:00:00+00:00')"
        ), {"hash": "f" * 64})
        connection.execute(text(
            "INSERT INTO cycle_annual_closing_reviews "
            "(closing_id,cycle_id,cash_evidence_id,review_version,process_revision,closing_cutoff_at,"
            "calculation_version,calculation_hash,ledger_cash_balance,actual_cash_balance,reconciliation_difference,"
            "participant_payout_liability,administration_fee,required_liquidity,liquidity_surplus,"
            "cash_evidence_reference,cash_evidence_hash,reconciliation_payload,reconciliation_hash,review_hash,created_by) "
            "SELECT 2,2,2,review_version,process_revision,closing_cutoff_at,calculation_version,calculation_hash,"
            "ledger_cash_balance,actual_cash_balance,reconciliation_difference,participant_payout_liability,"
            "administration_fee,required_liquidity,liquidity_surplus,cash_evidence_reference,cash_evidence_hash,"
            "reconciliation_payload,reconciliation_hash,:hash,created_by "
            "FROM cycle_annual_closing_reviews WHERE id=:review_id"
        ), {"hash": "9" * 64, "review_id": review_id})
        other_review_id = connection.execute(text(
            "SELECT id FROM cycle_annual_closing_reviews WHERE closing_id=2"
        )).scalar_one()
        with pytest.raises(sa.exc.DatabaseError):
            with connection.begin_nested():
                connection.execute(text(
                    "UPDATE cycle_annual_closings SET approved_review_id=:review_id WHERE id=1"
                ), {"review_id": other_review_id})

        connection.execute(text("UPDATE cycle_annual_closings SET approved_review_id=:id WHERE id=1"), {"id": review_id})
        with pytest.raises(sa.exc.DatabaseError):
            with connection.begin_nested():
                connection.execute(text("UPDATE cycle_annual_closings SET approved_review_id=NULL WHERE id=1"))
        for sql in (
            "UPDATE cycle_annual_closing_reviews SET actual_cash_balance=1 WHERE id=1",
            "DELETE FROM cycle_annual_closing_reviews WHERE id=1",
        ):
            with pytest.raises(sa.exc.DatabaseError):
                with connection.begin_nested():
                    connection.execute(text(sql))
        for sql in (
            "UPDATE cycle_annual_closing_cash_evidence SET declared_cash_balance=1 WHERE id=1",
            "DELETE FROM cycle_annual_closing_cash_evidence WHERE id=1",
        ):
            with pytest.raises(sa.exc.DatabaseError):
                with connection.begin_nested():
                    connection.execute(text(sql))
        with pytest.raises(sa.exc.DatabaseError):
            with connection.begin_nested():
                connection.execute(text(
                    "UPDATE cycle_annual_closings SET approved_review_id=999 WHERE id=1"
                ))
    engine.dispose()

    _run_migration(command.downgrade, cfg, url, PARENT)
    engine = sa.create_engine(url)
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert "cycle_annual_closing_reviews" not in inspector.get_table_names()
        assert "cycle_annual_closing_cash_evidence" not in inspector.get_table_names()
        assert "approved_review_id" not in {col["name"] for col in inspector.get_columns("cycle_annual_closings")}
        assert "ix_cycle_annual_closings_status" in {
            index["name"] for index in inspector.get_indexes("cycle_annual_closings")
        }
        assert connection.execute(text("SELECT count(*) FROM cycle_annual_closings WHERE id=1")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM cycle_annual_closing_snapshots")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM cycle_realized_gain_events")).scalar_one() == 1
        if connection.dialect.name == "sqlite":
            triggers = set(connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )).scalars())
            assert "trg_cycle_annual_snapshots_no_update" in triggers
            assert "trg_cycle_annual_snapshots_no_delete" in triggers
            assert "trg_cycle_gain_events_full_reversal" in triggers
    engine.dispose()

    _run_migration(command.upgrade, cfg, url, REVISION)
    engine = sa.create_engine(url)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == REVISION
        assert connection.execute(text("SELECT count(*) FROM cycle_annual_closings WHERE id=1")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM cycle_annual_closing_reviews")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM cycle_annual_closing_snapshots")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM cycle_realized_gain_events")).scalar_one() == 1
    engine.dispose()


def test_0099_sqlite_roundtrip(tmp_path):
    roundtrip(f"sqlite:///{tmp_path / 'a377b3r1_roundtrip.db'}")


def test_0099_postgresql_roundtrip_if_configured():
    raw_url = os.environ.get("A377B1_POSTGRES_TEST_URL")
    if not raw_url:
        pytest.skip("A377B1_POSTGRES_TEST_URL is not configured")
    base_url = sa.engine.make_url(raw_url)
    if base_url.get_backend_name() != "postgresql" or "a377b1_test" not in (base_url.database or "").lower():
        pytest.fail("PostgreSQL URL must target a dedicated a377b1_test database")
    schema = "a377b3r1_" + uuid4().hex[:12]
    admin_engine = sa.create_engine(raw_url, pool_pre_ping=True)
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        isolated = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
        roundtrip(isolated.render_as_string(hide_password=False))
    finally:
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
