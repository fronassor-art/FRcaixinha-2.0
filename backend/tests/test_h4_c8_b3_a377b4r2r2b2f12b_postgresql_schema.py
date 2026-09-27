"""PostgreSQL contract for the migrated F12B PIX persistence schema."""
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


DATABASE_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="requires the PostgreSQL CI database after alembic upgrade head",
)


def _insert_payment(conn, key, reference_type, reference_id, *, attempt_status="PENDING", complete_loan=False):
    fields = ""
    values = {}
    if complete_loan:
        fields = ", calculated_for_date, financial_snapshot_json, snapshot_hash, expires_at"
        values = {
            "calculated_for_date": "2026-09-27",
            "financial_snapshot_json": "{}",
            "snapshot_hash": "synthetic-hash",
            "expires_at": "2026-09-28 00:00:00+00",
        }
    conn.execute(text(f"""
        INSERT INTO payments (
            provider, provider_payment_id, idempotency_key, amount, status,
            reference_type, reference_id, attempt_status, created_at{fields}
        ) VALUES (
            'mercado_pago', NULL, :key, 1, 'PENDING',
            :reference_type, :reference_id, :attempt_status, now(){',' if complete_loan else ''}
            {', '.join(':' + name for name in values)}
        )
    """), {
        "key": key,
        "reference_type": reference_type,
        "reference_id": reference_id,
        "attempt_status": attempt_status,
        **values,
    })


def test_postgresql_migration_installs_constraint_index_and_nullable_resource_id():
    engine = create_engine(DATABASE_URL)
    token = uuid.uuid4().hex
    try:
        with engine.connect() as conn:
            tx = conn.begin()
            try:
                # Valid placeholders for the two newly allowed reference types.
                _insert_payment(conn, f"f12b-c-{token}", "CONTRIBUTION", f"c-{token}")
                _insert_payment(conn, f"f12b-a-{token}", "AGREEMENT_INSTALLMENT", f"a-{token}")
                _insert_payment(
                    conn, f"f12b-l-{token}", "LOAN_INSTALLMENT", f"l-{token}", complete_loan=True
                )

                # The existing generic partial index rejects a second active attempt.
                with pytest.raises(IntegrityError):
                    with conn.begin_nested():
                        _insert_payment(conn, f"f12b-c2-{token}", "CONTRIBUTION", f"c-{token}")

                # Unknown reference types and incomplete loan snapshots remain blocked.
                with pytest.raises(IntegrityError):
                    with conn.begin_nested():
                        _insert_payment(conn, f"f12b-x-{token}", "UNSUPPORTED", f"x-{token}")
                with pytest.raises(IntegrityError):
                    with conn.begin_nested():
                        _insert_payment(conn, f"f12b-l2-{token}", "LOAN_INSTALLMENT", f"l2-{token}")

                event_id = f"f12b-event-{token}"
                resource_id = f"synthetic-order-{token}"
                conn.execute(text("""
                    INSERT INTO webhook_events
                        (provider, event_id, event_type, resource_id, processed, created_at)
                    VALUES ('mercado_pago', :event_id, 'order', :resource_id, false, now())
                """), {"event_id": event_id, "resource_id": resource_id})
                assert conn.execute(
                    text("SELECT resource_id FROM webhook_events WHERE event_id=:event_id"),
                    {"event_id": event_id},
                ).scalar_one() == resource_id

                inspector = __import__("sqlalchemy").inspect(conn)
                columns = {column["name"]: column for column in inspector.get_columns("webhook_events")}
                assert columns["resource_id"]["nullable"] is True
                indexes = {item["name"]: item for item in inspector.get_indexes("payments")}
                pending = indexes["uq_payments_reference_pending"]
                assert pending["unique"] is True
                assert "attempt_status = 'PENDING'" in pending["dialect_options"]["postgresql_where"]
            finally:
                tx.rollback()
    finally:
        engine.dispose()
