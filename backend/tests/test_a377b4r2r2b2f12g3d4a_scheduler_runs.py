from datetime import date, datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from alembic.runtime.migration import MigrationContext
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import SchedulerRun, SchedulerRunUnit
from app.services.scheduler_runs import (
    SCHEDULER_FINANCIAL_DATE_SOURCE,
    SchedulerLeaseLost,
    claim_run,
    claim_unit,
    get_or_create_run,
    get_or_create_unit,
    mark_run_failed,
    mark_run_succeeded,
    mark_unit_failed,
    mark_unit_succeeded,
)


NOW = datetime(2027, 1, 10, 4, 5, tzinfo=timezone.utc)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def make_run(db, *, job="cycle", day=date(2027, 1, 10)):
    return get_or_create_run(
        db, job_key=job, financial_date=day, scheduled_for=NOW
    )[0]


def test_run_creation_is_idempotent_and_unique_by_job_and_financial_date(db):
    first, created = get_or_create_run(
        db, job_key="daily", financial_date=date(2027, 1, 10), scheduled_for=NOW
    )
    again, created_again = get_or_create_run(
        db, job_key="daily", financial_date=date(2027, 1, 10),
        scheduled_for=NOW + timedelta(minutes=1),
    )
    next_day, next_created = get_or_create_run(
        db, job_key="daily", financial_date=date(2027, 1, 11), scheduled_for=NOW
    )
    other_job, other_created = get_or_create_run(
        db, job_key="cycle", financial_date=date(2027, 1, 10), scheduled_for=NOW
    )

    assert created is True
    assert created_again is False
    assert first.id == again.id
    assert again.scheduled_for == NOW
    assert next_created is True and next_day.id != first.id
    assert other_created is True and other_job.id != first.id
    assert db.query(SchedulerRun).count() == 3


def test_first_claim_wins_active_lease_blocks_second_and_counts_only_winner(db):
    run = make_run(db)
    assert claim_run(db, run.id, lease_owner="worker-a", now=NOW, lease_seconds=60)
    db.flush()
    assert not claim_run(
        db, run.id, lease_owner="worker-b", now=NOW + timedelta(seconds=30), lease_seconds=60
    )
    assert run.status == "RUNNING"
    assert run.lease_owner == "worker-a"
    assert run.lease_expires_at == NOW + timedelta(seconds=60)
    assert run.attempt_count == 1


def test_expired_run_lease_is_reclaimed_and_succeeded_is_terminal(db):
    run = make_run(db)
    assert claim_run(db, run.id, lease_owner="worker-a", now=NOW, lease_seconds=10)
    db.flush()
    assert claim_run(
        db, run.id, lease_owner="worker-b", now=NOW + timedelta(seconds=10), lease_seconds=20
    )
    assert (run.lease_owner, run.attempt_count) == ("worker-b", 2)
    mark_run_succeeded(db, run.id, lease_owner="worker-b", completed_at=NOW + timedelta(seconds=11))
    assert run.status == "SUCCEEDED"
    assert run.completed_at == NOW + timedelta(seconds=11)
    assert run.lease_owner is None and run.lease_expires_at is None
    assert not claim_run(db, run.id, lease_owner="worker-c", now=NOW + timedelta(seconds=12))
    assert run.attempt_count == 2


def test_failed_run_is_retryable_and_error_storage_uses_safe_code(db):
    run = make_run(db)
    assert claim_run(db, run.id, lease_owner="one", now=NOW)
    mark_run_failed(
        db, run.id, lease_owner="one", error_code="database_unavailable",
        failed_at=NOW + timedelta(seconds=1),
    )
    assert run.status == "FAILED"
    assert run.last_error == "database_unavailable"
    assert run.lease_owner is None and run.lease_expires_at is None
    assert claim_run(db, run.id, lease_owner="two", now=NOW + timedelta(seconds=2))
    assert run.attempt_count == 2
    mark_run_failed(
        db, run.id, lease_owner="two",
        error_code="Authorization: Bearer secret CPF 12345678901 amount=99.00",
        failed_at=NOW + timedelta(seconds=3),
    )
    assert run.last_error == "execution_failed"
    assert "secret" not in run.last_error
    assert len(run.last_error) <= 80


def test_stale_run_owner_cannot_mark_a_reclaimed_lease(db):
    run = make_run(db)
    claim_run(db, run.id, lease_owner="old", now=NOW, lease_seconds=1)
    db.flush()
    claim_run(db, run.id, lease_owner="new", now=NOW + timedelta(seconds=1), lease_seconds=30)
    with pytest.raises(SchedulerLeaseLost):
        mark_run_succeeded(db, run.id, lease_owner="old", completed_at=NOW + timedelta(seconds=2))


def test_unit_creation_claim_reclaim_and_terminal_states(db):
    run = make_run(db)
    assert claim_run(db, run.id, lease_owner="run-owner", now=NOW, lease_seconds=120)
    unit, created = get_or_create_unit(db, run_id=run.id, unit_key="participation:42")
    same, created_again = get_or_create_unit(db, run_id=run.id, unit_key="participation:42")
    second_unit, second_created = get_or_create_unit(db, run_id=run.id, unit_key="participation:43")

    assert created and not created_again and unit.id == same.id
    assert second_created and second_unit.id != unit.id
    assert db.query(SchedulerRunUnit).count() == 2
    assert claim_unit(db, unit.id, lease_owner="unit-a", now=NOW, lease_seconds=10)
    db.flush()
    assert not claim_unit(
        db, unit.id, lease_owner="unit-b", now=NOW + timedelta(seconds=5), lease_seconds=10
    )
    assert unit.attempt_count == 1
    assert claim_unit(
        db, unit.id, lease_owner="unit-b", now=NOW + timedelta(seconds=10), lease_seconds=10
    )
    assert unit.attempt_count == 2 and unit.lease_owner == "unit-b"
    mark_unit_succeeded(db, unit.id, lease_owner="unit-b", completed_at=NOW + timedelta(seconds=11))
    assert unit.status == "SUCCEEDED" and unit.completed_at == NOW + timedelta(seconds=11)
    assert not claim_unit(db, unit.id, lease_owner="unit-c", now=NOW + timedelta(seconds=12))
    assert unit.attempt_count == 2


def test_unit_failed_can_be_reclaimed_and_mark_failed_releases_lease(db):
    run = make_run(db)
    claim_run(db, run.id, lease_owner="run-owner", now=NOW)
    unit, _ = get_or_create_unit(db, run_id=run.id, unit_key="item-a")
    assert claim_unit(db, unit.id, lease_owner="unit-a", now=NOW)
    mark_unit_failed(db, unit.id, lease_owner="unit-a", error_code="validation_failed", failed_at=NOW + timedelta(seconds=1))
    assert unit.status == "FAILED"
    assert unit.last_error == "validation_failed"
    assert unit.lease_owner is None and unit.lease_expires_at is None
    assert claim_unit(db, unit.id, lease_owner="unit-b", now=NOW + timedelta(seconds=2))
    assert unit.attempt_count == 2


def test_run_cannot_succeed_while_units_are_incomplete_and_financial_date_contract_is_explicit(db):
    run = make_run(db)
    claim_run(db, run.id, lease_owner="run-owner", now=NOW)
    unit, _ = get_or_create_unit(db, run_id=run.id, unit_key="participant-1")
    assert SCHEDULER_FINANCIAL_DATE_SOURCE == "financial_civil_date(timezone-aware datetime)"
    assert run.financial_date == date(2027, 1, 10)
    with pytest.raises(ValueError, match="incomplete units"):
        mark_run_succeeded(db, run.id, lease_owner="run-owner", completed_at=NOW + timedelta(seconds=1))
    assert unit.status == "PENDING"


def test_claim_requires_valid_owner_lease_and_aware_timestamps(db):
    run = make_run(db)
    with pytest.raises(ValueError):
        claim_run(db, run.id, lease_owner="", now=NOW)
    with pytest.raises(ValueError):
        claim_run(db, run.id, lease_owner="worker", now=datetime(2027, 1, 10))
    with pytest.raises(ValueError):
        claim_run(db, run.id, lease_owner="worker", now=NOW, lease_seconds=0)
    assert run.status == "PENDING" and run.attempt_count == 0


def test_scheduler_migration_upgrades_and_downgrades_structurally(tmp_path):
    pytest.importorskip("pydantic_settings")
    from app.core.config import settings

    database_path = tmp_path / "scheduler-migration.db"
    previous_url = settings.database_url
    settings.database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "head")
        engine = create_engine(settings.database_url)
        try:
            with engine.connect() as connection:
                current = MigrationContext.configure(connection).get_current_revision()
                assert current == "0104_scheduler_runs_g3d4a"
                tables = set(inspect(connection).get_table_names())
                assert {"scheduler_runs", "scheduler_run_units"} <= tables
                run_constraints = {c["name"] for c in inspect(connection).get_unique_constraints("scheduler_runs")}
                unit_constraints = {c["name"] for c in inspect(connection).get_unique_constraints("scheduler_run_units")}
                assert "uq_scheduler_runs_job_financial_date" in run_constraints
                assert "uq_scheduler_run_units_run_unit" in unit_constraints
                run_checks = {c["name"] for c in inspect(connection).get_check_constraints("scheduler_runs")}
                unit_checks = {c["name"] for c in inspect(connection).get_check_constraints("scheduler_run_units")}
                assert {"ck_scheduler_runs_status", "ck_scheduler_runs_attempt_nonnegative", "ck_scheduler_runs_lease_state", "ck_scheduler_runs_completion_state"} <= run_checks
                assert {"ck_scheduler_run_units_status", "ck_scheduler_run_units_attempt_nonnegative", "ck_scheduler_run_units_lease_state", "ck_scheduler_run_units_completion_state"} <= unit_checks
                assert {i["name"] for i in inspect(connection).get_indexes("scheduler_runs")} == {"ix_scheduler_runs_status_lease"}
                assert {i["name"] for i in inspect(connection).get_indexes("scheduler_run_units")} == {"ix_scheduler_run_units_run_status_lease"}
                unit_fks = inspect(connection).get_foreign_keys("scheduler_run_units")
                assert any(fk["referred_table"] == "scheduler_runs" and fk["options"].get("ondelete") == "CASCADE" for fk in unit_fks)
            command.downgrade(config, "0103_pix_reconciliation_schema_a377b4r4")
            with engine.connect() as connection:
                tables = set(inspect(connection).get_table_names())
                assert "scheduler_runs" not in tables
                assert "scheduler_run_units" not in tables
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert MigrationContext.configure(connection).get_current_revision() == "0104_scheduler_runs_g3d4a"
        finally:
            engine.dispose()
    finally:
        settings.database_url = previous_url
