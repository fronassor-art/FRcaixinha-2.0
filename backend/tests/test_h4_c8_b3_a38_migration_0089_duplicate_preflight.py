import os
import subprocess
import sys

from sqlalchemy import create_engine, event, text


BASE_REVISION = "0088_monthly_closing_snapshot_schema_h3c_b2"
TARGET_REVISION = "0089_collection_agreement_subjects"


def _upgrade(database, revision, *, check=True):
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{database}",
            "JWT_SECRET": "testsecret",
            "APP_ENV": "test",
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


def _engine(database):
    engine = create_engine(f"sqlite:///{database}")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _seed_loan_case(engine, *, duplicate=False):
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
        values = "(1, 1, 1, 'OPEN', 'SOFT', '2099-01-01')"
        cases = [values]
        if duplicate:
            cases.append("(2, 1, 1, 'OPEN', 'SOFT', '2099-01-02')")
        connection.execute(
            text(
                "INSERT INTO collection_cases "
                "(id, member_id, loan_id, status, stage, opened_at) VALUES "
                + ", ".join(cases)
            )
        )


def _version(engine):
    with engine.begin() as connection:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def _index_names(engine, table):
    with engine.begin() as connection:
        return {row[1] for row in connection.execute(text(f"PRAGMA index_list('{table}')"))}


def test_0089_duplicate_open_loan_case_preflight(tmp_path):
    compatible_database = tmp_path / "compatible.db"
    _upgrade(compatible_database, BASE_REVISION)
    compatible_engine = _engine(compatible_database)
    _upgrade(compatible_database, TARGET_REVISION)
    assert _version(compatible_engine) == TARGET_REVISION
    assert "uq_collection_case_open_loan_subject" in _index_names(
        compatible_engine, "collection_cases"
    )
    with compatible_engine.begin() as connection:
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []

    duplicate_database = tmp_path / "duplicate.db"
    _upgrade(duplicate_database, BASE_REVISION)
    duplicate_engine = _engine(duplicate_database)
    _seed_loan_case(duplicate_engine, duplicate=True)
    with duplicate_engine.begin() as connection:
        assert connection.execute(
            text(
                "SELECT COUNT(*) FROM collection_cases "
                "WHERE member_id = 1 AND loan_id = 1 AND status = 'OPEN'"
            )
        ).scalar_one() == 2

    result = _upgrade(duplicate_database, TARGET_REVISION, check=False)
    output = (result.stdout + result.stderr).lower()
    assert result.returncode != 0
    assert "cannot upgrade 0089" in output
    assert "collection_cases" in output
    assert "duplicate" in output
    assert "open" in output
    assert "member_id" in output
    assert "loan_id" in output
    assert "resolve" in output
    assert "create unique index" not in output
    assert _version(duplicate_engine) == BASE_REVISION
    with duplicate_engine.begin() as connection:
        assert "agreement_installment_id" not in {
            row[1] for row in connection.execute(text("PRAGMA table_info(collection_events)"))
        }
        assert "agreement_id" not in {
            row[1] for row in connection.execute(text("PRAGMA table_info(collection_cases)"))
        }
        assert "uq_collection_case_open_loan_subject" not in _index_names(
            duplicate_engine, "collection_cases"
        )
        assert connection.execute(
            text(
                "SELECT COUNT(*) FROM collection_cases "
                "WHERE member_id = 1 AND loan_id = 1 AND status = 'OPEN'"
            )
        ).scalar_one() == 2

    single_database = tmp_path / "single.db"
    _upgrade(single_database, BASE_REVISION)
    single_engine = _engine(single_database)
    _seed_loan_case(single_engine)
    _upgrade(single_database, TARGET_REVISION)
    assert _version(single_engine) == TARGET_REVISION
    assert "uq_collection_case_open_loan_subject" in _index_names(
        single_engine, "collection_cases"
    )
