"""PostgreSQL contract for the migrated F12B PIX persistence schema."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Barrier

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import SchedulerRun, SchedulerRunUnit
from app.services.scheduler_runs import (
    SchedulerLeaseLost,
    claim_run,
    claim_unit,
    get_or_create_run,
    get_or_create_unit,
    mark_unit_succeeded,
)


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
                assert bool(pending["unique"])
                predicate = str(pending["dialect_options"]["postgresql_where"])
                assert "attempt_status" in predicate and "PENDING" in predicate
            finally:
                tx.rollback()
    finally:
        engine.dispose()


def test_postgresql_scheduler_run_and_unit_create_and_claim_have_one_winner():
    """Exercise scheduler uniqueness and row-lock claims with independent PG sessions."""
    engine = create_engine(DATABASE_URL, pool_size=5, max_overflow=0)
    Sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    token = uuid.uuid4().hex
    job_key = f"g3d4a-{token}"
    financial_date = date(2027, 1, 10)
    scheduled_for = datetime(2027, 1, 10, 4, 5, tzinfo=timezone.utc)

    def concurrent_run_create(_index):
        gate.wait(timeout=10)
        with Sessions() as session:
            row, created = get_or_create_run(
                session,
                job_key=job_key,
                financial_date=financial_date,
                scheduled_for=scheduled_for,
            )
            run_id = row.id
            session.commit()
            return run_id, created

    def concurrent_run_claim(index):
        gate.wait(timeout=10)
        with Sessions() as session:
            won = claim_run(
                session,
                run_id,
                lease_owner=f"run-owner-{index}-{token}",
                now=scheduled_for,
                lease_seconds=60,
            )
            session.commit()
            return won

    def concurrent_unit_create(_index):
        gate.wait(timeout=10)
        with Sessions() as session:
            row, created = get_or_create_unit(session, run_id=run_id, unit_key="participant:42")
            unit_id = row.id
            session.commit()
            return unit_id, created

    def concurrent_unit_claim(index):
        gate.wait(timeout=10)
        with Sessions() as session:
            won = claim_unit(
                session,
                unit_id,
                lease_owner=run_owner,
                now=scheduled_for,
                lease_seconds=60,
            )
            session.commit()
            return won

    unit_id = None
    try:
        gate = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            run_results = list(pool.map(concurrent_run_create, range(2)))
        assert run_results[0][0] == run_results[1][0]
        assert sorted(created for _, created in run_results) == [False, True]
        run_id = run_results[0][0]

        with Sessions() as session:
            assert session.query(SchedulerRun).filter_by(job_key=job_key, financial_date=financial_date).count() == 1

        gate = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            run_claims = list(pool.map(concurrent_run_claim, range(2)))
        assert sum(run_claims) == 1
        run_owner = f"run-owner-{run_claims.index(True)}-{token}"

        gate = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            unit_results = list(pool.map(concurrent_unit_create, range(2)))
        assert unit_results[0][0] == unit_results[1][0]
        assert sorted(created for _, created in unit_results) == [False, True]
        unit_id = unit_results[0][0]

        with Sessions() as session:
            assert session.query(SchedulerRunUnit).filter_by(run_id=run_id, unit_key="participant:42").count() == 1

        gate = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            unit_claims = list(pool.map(concurrent_unit_claim, range(2)))
        assert sum(unit_claims) == 1
    finally:
        with Sessions() as session:
            session.execute(
                text("DELETE FROM scheduler_runs WHERE job_key=:job_key AND financial_date=:financial_date"),
                {"job_key": job_key, "financial_date": financial_date},
            )
            session.commit()
        engine.dispose()


def test_postgresql_reclaimed_parent_fences_stale_unit_completion():
    """A stale unit claimant cannot complete after another owner reclaims its run."""
    engine = create_engine(DATABASE_URL)
    Sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    token = uuid.uuid4().hex
    job_key = f"g3d4a-fence-{token}"
    scheduled_for = datetime(2027, 1, 10, 4, 5, tzinfo=timezone.utc)
    financial_date = date(2027, 1, 10)
    try:
        with Sessions() as session:
            run, _ = get_or_create_run(
                session,
                job_key=job_key,
                financial_date=financial_date,
                scheduled_for=scheduled_for,
            )
            run_id = run.id
            assert claim_run(
                session, run_id, lease_owner="worker-a", now=scheduled_for,
                lease_seconds=10,
            )
            session.commit()

        with Sessions() as session:
            unit, _ = get_or_create_unit(session, run_id=run_id, unit_key="participant:42")
            unit_id = unit.id
            assert claim_unit(
                session, unit_id, lease_owner="worker-a", now=scheduled_for,
                lease_seconds=120,
            )
            session.commit()

        with Sessions() as session:
            assert claim_run(
                session, run_id, lease_owner="worker-b",
                now=scheduled_for + timedelta(seconds=10), lease_seconds=120,
            )
            session.commit()

        with Sessions() as session:
            with pytest.raises(SchedulerLeaseLost):
                mark_unit_succeeded(
                    session, unit_id, lease_owner="worker-a",
                    completed_at=scheduled_for + timedelta(seconds=11),
                )
            session.rollback()

        with Sessions() as session:
            run = session.get(SchedulerRun, run_id)
            unit = session.get(SchedulerRunUnit, unit_id)
            assert run.status == "RUNNING" and run.lease_owner == "worker-b"
            assert unit.status == "RUNNING" and unit.lease_owner == "worker-a"
            assert unit.completed_at is None
    finally:
        with Sessions() as session:
            session.execute(
                text("DELETE FROM scheduler_runs WHERE job_key=:job_key AND financial_date=:financial_date"),
                {"job_key": job_key, "financial_date": financial_date},
            )
            session.commit()
        engine.dispose()
