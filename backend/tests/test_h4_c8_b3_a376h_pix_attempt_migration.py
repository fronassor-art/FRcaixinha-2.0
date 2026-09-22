"""0095 contract tests; run under the Debian backend environment."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
import pytest
from sqlalchemy import CheckConstraint, create_engine, inspect, text
from app.db.base import Base
from app.models import Payment

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("m0095", ROOT / "alembic/versions/0095_pix_attempt_provider_reservation.py")
module = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(module)

def test_revision_chain_and_strong_reservation_contract():
    assert module.revision == "0095_pix_attempt_provider_reservation"
    assert module.down_revision == "0094_late_interest_event_contract"
    for token in ("reference_type = 'LOAN_INSTALLMENT'", "reference_id IS NOT NULL", "attempt_status IS NOT NULL", "idempotency_key IS NOT NULL", "calculated_for_date IS NOT NULL", "financial_snapshot_json IS NOT NULL", "snapshot_hash IS NOT NULL", "expires_at IS NOT NULL"):
        assert token in module.CHECK
    assert "INSERT" not in Path(module.__file__).read_text().upper()
    assert "UPDATE " not in Path(module.__file__).read_text().upper()

def test_model_has_equivalent_named_check_and_nullable_provider_id():
    assert Payment.__table__.c.provider_payment_id.nullable
    checks={c.name: str(c.sqltext) for c in Payment.__table__.constraints if isinstance(c, CheckConstraint)}
    assert checks[module.CHECK_NAME] == module.CHECK

def test_sqlite_check_allows_only_complete_versioned_reservation():
    engine=create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        # Provider-bound legacy records need no versioned metadata.
        conn.execute(text("INSERT INTO payments (provider, provider_payment_id, idempotency_key, amount, status,created_at) VALUES ('x','legacy','legacy-key',1,'PENDING','2026-01-01 00:00:00')"))
        # A complete versioned reservation may have no provider ID.
        conn.execute(text("INSERT INTO payments (provider, provider_payment_id, idempotency_key, amount, status, reference_type, reference_id, attempt_status, calculated_for_date, financial_snapshot_json, snapshot_hash, expires_at, created_at) VALUES ('x',NULL,'reserve-key',1,'PENDING','LOAN_INSTALLMENT','1','PENDING','2026-01-01','{}','hash','2026-01-02 03:00:00','2026-01-01 00:00:00')"))
        for ref in ("CONTRIBUTION", "AGREEMENT_INSTALLMENT", "LOAN_INSTALLMENT"):
            with pytest.raises(Exception):
                conn.execute(text("INSERT INTO payments (provider, provider_payment_id, idempotency_key, amount, status, reference_type, reference_id, attempt_status, created_at) VALUES ('x',NULL,:key,1,'PENDING',:ref,'1','PENDING','2026-01-01 00:00:00')"), {"key": "bad-"+ref, "ref": ref})


def test_migration_has_fail_closed_downgrade_and_no_backfill_keywords():
    source=Path(module.__file__).read_text()
    assert "SELECT COUNT(*) FROM payments WHERE provider_payment_id IS NULL" in source
    assert "Cannot downgrade 0095" in source
    for prohibited in ("INSERT INTO", "UPDATE payments", "DELETE FROM", "backfill"):
        assert prohibited.lower() not in source.lower()

def test_provider_bound_contribution_and_agreement_do_not_require_attempt_metadata():
    engine=create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for ref, key in (("CONTRIBUTION", "contrib-bound"), ("AGREEMENT_INSTALLMENT", "agreement-bound")):
            conn.execute(text("INSERT INTO payments (provider, provider_payment_id, idempotency_key, amount, status, reference_type, reference_id, created_at) VALUES ('x',:pid,:key,1,'PENDING',:ref,'1','2026-01-01 00:00:00')"), {"pid": key, "key": key, "ref": ref})


def _backend_dir(): return Path(__file__).resolve().parents[1]
def _alembic(database, *args):
    env=os.environ.copy(); env.update({"DATABASE_URL": f"sqlite:///{database}", "JWT_SECRET":"testsecret", "APP_ENV":"test"})
    result=subprocess.run([sys.executable,"-m","alembic",*args],cwd=_backend_dir(),env=env,capture_output=True,text=True)
    if result.returncode and "pydantic_settings" in result.stderr:
        pytest.skip("ENVIRONMENT_BLOCKED: pydantic_settings unavailable in Termux")
    result.check_returncode()

def _reservation_sql(key="reservation"): return {"key":key}

def test_real_0094_to_0095_sqlite_upgrade_constraint_and_safe_downgrade(tmp_path):
    database=tmp_path/"a376h_0095.db"
    _alembic(database,"upgrade","0094_late_interest_event_contract")
    engine=create_engine(f"sqlite:///{database}")
    with engine.begin() as c:
        c.execute(text("INSERT INTO payments (provider,provider_payment_id,idempotency_key,amount,status,reference_type,reference_id,created_at) VALUES ('legacy','legacy-id','legacy-key',1,'PENDING','CONTRIBUTION','1','2026-01-01 00:00:00')"))
    engine.dispose(); _alembic(database,"upgrade","0095_pix_attempt_provider_reservation")
    engine=create_engine(f"sqlite:///{database}")
    columns = {column["name"]: column for column in inspect(engine).get_columns("payments")}
    assert "provider_payment_id" in columns
    assert columns["provider_payment_id"]["nullable"] is True
    assert {x["name"]:x for x in inspect(engine).get_columns("payments")}["provider_payment_id"]["nullable"]
    with engine.begin() as c:
        assert c.execute(text("SELECT provider_payment_id FROM payments WHERE idempotency_key='legacy-key'")).scalar_one()=="legacy-id"
        c.execute(text("INSERT INTO payments (provider,provider_payment_id,idempotency_key,amount,status,reference_type,reference_id,attempt_status,calculated_for_date,financial_snapshot_json,snapshot_hash,expires_at,created_at) VALUES ('x',NULL,'reservation',1,'PENDING','LOAN_INSTALLMENT','1','PENDING','2026-01-01','{}','hash','2026-01-02 03:00:00','2026-01-01 00:00:00')"))
        for sql in (
            "INSERT INTO payments (provider,provider_payment_id,idempotency_key,amount,status,reference_type,reference_id) VALUES ('x',NULL,'bad-c',1,'PENDING','CONTRIBUTION','1')",
            "INSERT INTO payments (provider,provider_payment_id,idempotency_key,amount,status,reference_type,reference_id) VALUES ('x',NULL,'bad-a',1,'PENDING','AGREEMENT_INSTALLMENT','1')",
            "INSERT INTO payments (provider,provider_payment_id,idempotency_key,amount,status,reference_type,reference_id,attempt_status) VALUES ('x',NULL,'bad-l',1,'PENDING','LOAN_INSTALLMENT','1','PENDING')",
        ):
            with pytest.raises(Exception): c.execute(text(sql))
    engine.dispose()
    with pytest.raises(subprocess.CalledProcessError): _alembic(database,"downgrade","0094_late_interest_event_contract")
    engine=create_engine(f"sqlite:///{database}")
    with engine.connect() as c: assert c.execute(text("SELECT provider_payment_id FROM payments WHERE idempotency_key='reservation'")).scalar_one() is None
    engine.dispose()

def test_real_0095_downgrade_restores_not_null_without_reservation(tmp_path):
    database=tmp_path/"a376h_0095_down.db"
    _alembic(database,"upgrade","0095_pix_attempt_provider_reservation")
    _alembic(database,"downgrade","0094_late_interest_event_contract")
    engine=create_engine(f"sqlite:///{database}")
    assert not {x["name"]:x for x in inspect(engine).get_columns("payments")}["provider_payment_id"]["nullable"]
