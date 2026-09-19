import os
import sqlite3
import subprocess
import sys
import threading
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError, PendingRollbackError
from sqlalchemy.orm import Query, Session, sessionmaker

from app.models import CollectionCase, Group, Loan, Member, User
from app.services import collection_recovery_v049
from app.services.collection_recovery_v049 import _open_case


BASE_REVISION = "0088_monthly_closing_snapshot_schema_h3c_b2"
TARGET_REVISION = "0089_collection_agreement_subjects"
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _upgrade(database, revision):
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{database}",
            "JWT_SECRET": "testsecret",
            "APP_ENV": "test",
        }
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=BACKEND_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _engine(database):
    engine = create_engine(
        f"sqlite:///{database}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _seed_member_and_loan(engine):
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, name, email, cpf, password_hash, role, is_active, created_at) "
                "VALUES (1, 'Synthetic', 'a@example.invalid', '00000000001', "
                "'hash', 'USER', 1, '2099-01-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO groups "
                "(id, name, monthly_amount, months, due_day, active, min_cash_reserve, "
                "max_simultaneous_loans, max_installments, grace_days, "
                "max_overdue_installments) "
                "VALUES (1, 'Synthetic', 100, 12, 10, 1, 0, 3, 6, 0, 12)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO members (id, user_id, group_id, status, joined_at) "
                "VALUES (1, 1, 1, 'ACTIVE', '2099-01-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO loans "
                "(id, member_id, principal, principal_settled_with_own_balance, "
                "monthly_rate, installments, status, requested_at) "
                "VALUES (1, 1, 100, 0, 0.01, 1, 'ACTIVE', '2099-01-01')"
            )
        )


def _open_case_count(engine):
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT COUNT(*) FROM collection_cases "
                "WHERE member_id = 1 AND loan_id = 1 AND status = 'OPEN'"
            )
        ).scalar_one()


def _run_concurrent_open_case(engine, before_open=None):
    sessions = sessionmaker(bind=engine)
    select_barrier = threading.Barrier(2)
    observed_absence = []
    results = []
    result_lock = threading.Lock()
    original_first = Query.first
    select_count = 0

    def first_after_select(query):
        nonlocal select_count
        result = original_first(query)
        statement = str(query.statement).upper()
        with result_lock:
            is_open_case_query = (
                "COLLECTION_CASES" in statement
                and "MEMBER_ID" in statement
                and "STATUS" in statement
                and select_count < 2
            )
            if is_open_case_query:
                select_count += 1
                observed_absence.append(result is None)
        if is_open_case_query:
            select_barrier.wait(timeout=30)
        return result

    Query.first = first_after_select

    def worker(label):
        session = sessions()
        result = {"label": label}
        try:
            if before_open is not None:
                before_open(session, label)
                with session.no_autoflush:
                    case, created = _open_case(session, 1, 1, stage="SOFT")
            else:
                case, created = _open_case(session, 1, 1, stage="SOFT")
            result.update(
                returned="OK",
                created=created,
                case_id=case.id,
                usable_count=session.query(CollectionCase).filter(
                    CollectionCase.member_id == 1,
                    CollectionCase.loan_id == 1,
                    CollectionCase.status == "OPEN",
                ).count(),
            )
            session.commit()
            result["commit"] = "OK"
        except Exception as exc:
            result.update(
                returned="ERROR",
                exception_type=type(exc).__name__,
                exception_message=str(exc),
            )
            try:
                session.rollback()
            except Exception:
                pass
        finally:
            session.close()
            with result_lock:
                results.append(result)

    try:
        threads = [
            threading.Thread(target=worker, args=("T1",), name="T1"),
            threading.Thread(target=worker, args=("T2",), name="T2"),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert all(not thread.is_alive() for thread in threads)
    finally:
        Query.first = original_first
    return observed_absence, results


def test_concurrent_open_case_converges_on_unique_winner(tmp_path):
    """The savepoint converts the expected race into the serial result."""
    database = tmp_path / "collection_case_concurrency.db"
    _upgrade(database, BASE_REVISION)
    _upgrade(database, TARGET_REVISION)
    engine = _engine(database)
    _seed_member_and_loan(engine)

    with engine.connect() as connection:
        assert connection.execute(
            text("PRAGMA foreign_keys")
        ).scalar_one() == 1
        index_sql = connection.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'index' AND name = 'uq_collection_case_open_loan_subject'"
            )
        ).scalar_one()
        assert "UNIQUE INDEX uq_collection_case_open_loan_subject" in index_sql
        assert "member_id, loan_id" in index_sql
        assert "status = 'OPEN' AND loan_id IS NOT NULL" in index_sql

    assert _open_case_count(engine) == 0

    observed_absence, results = _run_concurrent_open_case(engine)
    assert observed_absence == [True, True]
    assert len(results) == 2
    assert all(result.get("returned") == "OK" for result in results)
    assert sum(result.get("commit") == "OK" for result in results) == 2
    assert sum(result.get("created") is True for result in results) == 1
    assert sum(result.get("created") is False for result in results) == 1
    assert len({result["case_id"] for result in results}) == 1
    assert all(result["usable_count"] == 1 for result in results)

    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []
        assert connection.execute(
            text(
                "SELECT COUNT(*) FROM collection_cases "
                "WHERE member_id = 1 AND loan_id = 1 AND status = 'OPEN'"
            )
        ).scalar_one() == 1


def test_serial_open_case_keeps_existing_semantics(tmp_path):
    database = tmp_path / "collection_case_serial.db"
    _upgrade(database, BASE_REVISION)
    _upgrade(database, TARGET_REVISION)
    engine = _engine(database)
    _seed_member_and_loan(engine)
    session = sessionmaker(bind=engine)()
    first, first_created = _open_case(session, 1, 1, stage="SOFT")
    session.commit()
    second, second_created = _open_case(session, 1, 1, stage="SOFT")
    session.commit()
    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert _open_case_count(engine) == 1
    session.close()


def test_concurrent_open_case_preserves_outer_transaction_mutation(tmp_path):
    database = tmp_path / "collection_case_outer_transaction.db"
    _upgrade(database, BASE_REVISION)
    _upgrade(database, TARGET_REVISION)
    engine = _engine(database)
    _seed_member_and_loan(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, name, email, cpf, password_hash, role, is_active, created_at) "
                "VALUES (2, 'Before T1', 'b@example.invalid', '00000000002', "
                "'hash', 'USER', 1, '2099-01-01'), "
                "(3, 'Before T2', 'c@example.invalid', '00000000003', "
                "'hash', 'USER', 1, '2099-01-01')"
            )
        )

    def mutate_before_open(session, label):
        user = session.get(User, 2 if label == "T1" else 3)
        user.name = f"Changed {label}"

    observed_absence, results = _run_concurrent_open_case(
        engine, before_open=mutate_before_open
    )
    assert observed_absence == [True, True]
    assert all(result.get("returned") == "OK" for result in results)
    assert sum(result.get("created") is True for result in results) == 1
    assert sum(result.get("created") is False for result in results) == 1
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT name FROM users WHERE id = 2")
        ).scalar_one() == "Changed T1"
        assert connection.execute(
            text("SELECT name FROM users WHERE id = 3")
        ).scalar_one() == "Changed T2"
        assert connection.execute(
            text(
                "SELECT COUNT(*) FROM collection_cases "
                "WHERE member_id = 1 AND loan_id = 1 AND status = 'OPEN'"
            )
        ).scalar_one() == 1


def test_unrelated_integrity_error_is_not_absorbed(tmp_path, monkeypatch):
    database = tmp_path / "collection_case_unrelated_integrity.db"
    _upgrade(database, BASE_REVISION)
    _upgrade(database, TARGET_REVISION)
    engine = _engine(database)
    _seed_member_and_loan(engine)
    original_flush = Session.flush

    def raise_foreign_key_error(session, *args, **kwargs):
        if any(isinstance(item, CollectionCase) for item in session.new):
            raise IntegrityError(
                "INSERT collection_cases",
                {},
                sqlite3.IntegrityError("FOREIGN KEY constraint failed"),
            )
        return original_flush(session, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", raise_foreign_key_error)
    session = sessionmaker(bind=engine)()
    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
        _open_case(session, 1, 1, stage="SOFT")
    session.rollback()
    session.close()


def test_expected_postgresql_constraint_classification_is_strict():
    class Diagnostics:
        constraint_name = "uq_collection_case_open_loan_subject"

    class Original:
        diag = Diagnostics()
        sqlstate = "23505"

    expected = IntegrityError("INSERT", {}, Original())
    assert collection_recovery_v049._is_open_case_unique_violation(expected)

    class OtherDiagnostics:
        constraint_name = "other_constraint"

    class OtherOriginal:
        diag = OtherDiagnostics()
        sqlstate = "23505"

    unrelated = IntegrityError("INSERT", {}, OtherOriginal())
    assert not collection_recovery_v049._is_open_case_unique_violation(unrelated)


def test_expected_conflict_without_winner_propagates_original_error(tmp_path, monkeypatch):
    database = tmp_path / "collection_case_missing_winner.db"
    _upgrade(database, BASE_REVISION)
    _upgrade(database, TARGET_REVISION)
    engine = _engine(database)
    _seed_member_and_loan(engine)
    original_flush = Session.flush

    def raise_expected_unique(session, *args, **kwargs):
        if any(isinstance(item, CollectionCase) for item in session.new):
            raise IntegrityError(
                "INSERT collection_cases",
                {},
                sqlite3.IntegrityError(
                    "UNIQUE constraint failed: "
                    "collection_cases.member_id, collection_cases.loan_id"
                ),
            )
        return original_flush(session, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", raise_expected_unique)
    session = sessionmaker(bind=engine)()
    with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
        _open_case(session, 1, 1, stage="SOFT")
    session.rollback()
    session.close()

    engine.dispose()
