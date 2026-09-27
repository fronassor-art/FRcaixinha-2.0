"""F12B persistence contract for PIX placeholders and webhook resource IDs."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Payment, WebhookEvent


ROOT = Path(__file__).parents[1]
MIGRATION_PATH = ROOT / "alembic/versions/0103_pix_reconciliation_schema_a377b4r4.py"
SPEC = importlib.util.spec_from_file_location("m0103", MIGRATION_PATH)
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


def _engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


def _insert_payment(conn, key, ref, reference_id="1", *, attempt="PENDING", provider_id=None, loan_complete=False):
    loan_fields = ""
    values = {}
    if loan_complete:
        loan_fields = ", calculated_for_date, financial_snapshot_json, snapshot_hash, expires_at"
        values.update({
            "calculated_for_date": "2026-09-27",
            "financial_snapshot_json": "{}",
            "snapshot_hash": "synthetic-hash",
            "expires_at": "2026-09-28 00:00:00",
        })
    conn.execute(text(f"""
        INSERT INTO payments (
            provider, provider_payment_id, idempotency_key, amount, status,
            reference_type, reference_id, attempt_status, created_at{loan_fields}
        ) VALUES (
            'mercado_pago', :provider_id, :key, 1, 'PENDING',
            :ref, :reference_id, :attempt, '2026-09-27 00:00:00'{',' if loan_complete else ''}
            {', '.join(':' + field for field in values)}
        )
    """), {
        "provider_id": provider_id,
        "key": key,
        "ref": ref,
        "reference_id": reference_id,
        "attempt": attempt,
        **values,
    })


def test_revision_chain_constraint_allowlist_and_loan_requirements():
    assert migration.revision == "0103_pix_reconciliation_schema_a377b4r4"
    assert migration.down_revision == "0102_payout_verification_evidence_a377b4r3"
    checks = {
        item.name: str(item.sqltext)
        for item in Payment.__table__.constraints
        if isinstance(item, CheckConstraint)
    }
    assert checks[migration.PAYMENT_CHECK] == migration.PAYMENT_CHECK_SQL
    assert "reference_type = 'CONTRIBUTION'" in migration.PAYMENT_CHECK_SQL
    assert "reference_type = 'AGREEMENT_INSTALLMENT'" in migration.PAYMENT_CHECK_SQL
    assert "reference_type = 'LOAN_INSTALLMENT'" in migration.PAYMENT_CHECK_SQL
    for required in (
        "calculated_for_date IS NOT NULL",
        "financial_snapshot_json IS NOT NULL",
        "snapshot_hash IS NOT NULL",
        "expires_at IS NOT NULL",
    ):
        assert required in migration.PAYMENT_CHECK_SQL


def test_placeholders_allow_only_supported_reference_types_and_complete_loan():
    engine = _engine()
    with engine.begin() as conn:
        _insert_payment(conn, "contrib-placeholder", "CONTRIBUTION")
        _insert_payment(conn, "agreement-placeholder", "AGREEMENT_INSTALLMENT")
        _insert_payment(conn, "loan-placeholder", "LOAN_INSTALLMENT", loan_complete=True)
        # A provider-bound legacy payment remains valid without attempt metadata.
        _insert_payment(conn, "bound-legacy", "UNRELATED_LEGACY_TYPE", provider_id="provider-id")

        invalid = (
            ("unknown-ref", "UNKNOWN_REFERENCE", "1", "PENDING", False),
            ("contrib-no-ref", "CONTRIBUTION", None, "PENDING", False),
            ("agreement-no-attempt", "AGREEMENT_INSTALLMENT", "3", None, False),
            ("loan-no-snapshot", "LOAN_INSTALLMENT", "4", "PENDING", False),
        )
        for key, ref, reference_id, attempt, complete in invalid:
            with pytest.raises(IntegrityError):
                _insert_payment(
                    conn, key, ref, reference_id, attempt=attempt, loan_complete=complete
                )
    engine.dispose()


def test_existing_generic_pending_index_guards_contribution_and_agreement_attempts():
    engine = _engine()
    indexes = {item["name"]: item for item in inspect(engine).get_indexes("payments")}
    pending = indexes["uq_payments_reference_pending"]
    assert bool(pending["unique"])
    assert "attempt_status = 'PENDING'" in pending["dialect_options"]["sqlite_where"].text

    with engine.begin() as conn:
        _insert_payment(conn, "contrib-first", "CONTRIBUTION", "same-id")
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            _insert_payment(conn, "contrib-second", "CONTRIBUTION", "same-id")

    with engine.begin() as conn:
        _insert_payment(conn, "agreement-first", "AGREEMENT_INSTALLMENT", "same-id")
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            _insert_payment(conn, "agreement-second", "AGREEMENT_INSTALLMENT", "same-id")

    # The partial unique key is the pair (reference_type, reference_id).
    with engine.begin() as conn:
        _insert_payment(conn, "cross-type-contribution", "CONTRIBUTION", "1")
        _insert_payment(conn, "cross-type-agreement", "AGREEMENT_INSTALLMENT", "1")
    engine.dispose()


def test_webhook_resource_id_is_nullable_and_round_trips_without_raw_payload():
    engine = _engine()
    with Session(engine) as db:
        event = WebhookEvent(
            provider="mercado_pago",
            event_id="synthetic-event-01",
            event_type="order",
            resource_id="opaque-provider-order-987654321",
            processed=False,
        )
        legacy = WebhookEvent(
            provider="mercado_pago",
            event_id="synthetic-event-legacy",
            event_type="payment",
            resource_id=None,
            processed=False,
        )
        db.add_all([event, legacy])
        db.commit()
        event_id, legacy_id = event.id, legacy.id
        db.expire_all()
        assert db.get(WebhookEvent, event_id).resource_id == "opaque-provider-order-987654321"
        assert db.get(WebhookEvent, legacy_id).resource_id is None
    assert "resource_id" in {column["name"] for column in inspect(engine).get_columns("webhook_events")}
    assert WebhookEvent.__table__.c.resource_id.nullable
    engine.dispose()


def _alembic(database: Path, *args):
    env = os.environ.copy()
    env.update({
        "DATABASE_URL": f"sqlite:///{database}",
        "JWT_SECRET": "test-secret-only",
        "APP_ENV": "test",
    })
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode and "pydantic_settings" in result.stderr:
        pytest.skip("ENVIRONMENT_BLOCKED: pydantic_settings unavailable")
    result.check_returncode()
    return result.stdout


def test_real_sqlite_upgrade_preserves_legacy_rows_and_partial_index(tmp_path):
    database = tmp_path / "f12b-upgrade.db"
    _alembic(database, "upgrade", "0102_payout_verification_evidence_a377b4r3")
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO payments
                (provider, provider_payment_id, idempotency_key, amount, status,
                 reference_type, reference_id, created_at)
            VALUES ('mercado_pago', 'legacy-provider-id', 'legacy-payment-key', 1,
                    'PENDING', 'CONTRIBUTION', '91', '2026-09-27 00:00:00')
        """))
        conn.execute(text("""
            INSERT INTO webhook_events (provider, event_id, event_type, processed, created_at)
            VALUES ('mercado_pago', 'legacy-event', 'order', 0, '2026-09-27 00:00:00')
        """))
    engine.dispose()

    _alembic(database, "upgrade", "head")
    engine = create_engine(f"sqlite:///{database}")
    inspector = inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns("webhook_events")}
    assert columns["resource_id"]["nullable"] is True
    indexes = {item["name"]: item for item in inspector.get_indexes("payments")}
    assert bool(indexes["uq_payments_reference_pending"]["unique"])
    assert "attempt_status = 'PENDING'" in indexes["uq_payments_reference_pending"]["dialect_options"]["sqlite_where"].text
    with engine.connect() as conn:
        payment = conn.execute(text("SELECT provider_payment_id, attempt_status, reconciliation_status, external_reference FROM payments WHERE idempotency_key='legacy-payment-key'")).one()
        event = conn.execute(text("SELECT event_type, resource_id FROM webhook_events WHERE event_id='legacy-event'")).one()
        assert tuple(payment) == ("legacy-provider-id", None, None, None)
        assert tuple(event) == ("order", None)
        _insert_payment(conn, "post-upgrade-contribution", "CONTRIBUTION", "92")
        _insert_payment(conn, "post-upgrade-agreement", "AGREEMENT_INSTALLMENT", "93")
    engine.dispose()


def test_real_sqlite_downgrade_restores_original_constraint_and_schema(tmp_path):
    database = tmp_path / "f12b-downgrade.db"
    _alembic(database, "upgrade", "head")
    _alembic(database, "downgrade", "0102_payout_verification_evidence_a377b4r3")
    engine = create_engine(f"sqlite:///{database}")
    columns = {column["name"] for column in inspect(engine).get_columns("webhook_events")}
    assert "resource_id" not in columns
    with engine.connect() as conn:
        check_sql = conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='payments'")).scalar_one()
    assert "reference_type = 'LOAN_INSTALLMENT'" in check_sql
    assert "reference_type = 'CONTRIBUTION'" not in check_sql
    engine.dispose()
