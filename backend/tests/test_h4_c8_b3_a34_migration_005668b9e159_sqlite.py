import os
import subprocess
import sys

from sqlalchemy import create_engine, event, text


REVISION = "005668b9e159"
DOWN_REVISION = "d04267d34476"


def _upgrade(database, revision):
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
        check=True,
        capture_output=True,
        text=True,
    )


def _column(connection):
    columns = {
        row[1]: row
        for row in connection.execute(text("PRAGMA table_info(loans)"))
    }
    return columns["principal_settled_with_own_balance"]


def _engine(database):
    engine = create_engine(f"sqlite:///{database}")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _assert_final_column(engine):
    with engine.begin() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == REVISION
        column = _column(connection)
        assert column[2] == "NUMERIC(14, 2)"
        assert column[3] == 1
        assert column[4] is None


def test_migration_005668b9e159_sqlite_handles_empty_and_legacy_loans(tmp_path):
    empty_database = tmp_path / "empty.db"
    _upgrade(empty_database, DOWN_REVISION)
    empty_engine = _engine(empty_database)
    with empty_engine.begin() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == DOWN_REVISION
        assert "principal_settled_with_own_balance" not in {
            row[1] for row in connection.execute(text("PRAGMA table_info(loans)"))
        }
    _upgrade(empty_database, REVISION)
    _assert_final_column(empty_engine)

    legacy_database = tmp_path / "legacy.db"
    _upgrade(legacy_database, DOWN_REVISION)
    legacy_engine = _engine(legacy_database)
    with legacy_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, name, email, cpf, password_hash, role, is_active, created_at) "
                "VALUES (1, 'Synthetic', 'legacy@example.invalid', '00000000000', "
                "'hash', 'USER', 1, '2099-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO groups "
                "(id, name, monthly_amount, months, due_day, active, min_cash_reserve, "
                "max_simultaneous_loans, max_installments, grace_days, "
                "max_overdue_installments) "
                "VALUES (1, 'Synthetic', 100, 12, 10, 1, 0, 3, 12, 0, 12)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO members (id, user_id, group_id, status, joined_at) "
                "VALUES (1, 1, 1, 'ACTIVE', '2099-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO loans "
                "(id, member_id, principal, monthly_rate, installments, status, requested_at) "
                "VALUES (1, 1, 100, 0.01, 1, 'ACTIVE', '2099-01-01 00:00:00')"
            )
        )
    _upgrade(legacy_database, REVISION)
    with legacy_engine.begin() as connection:
        assert connection.execute(
            text("SELECT principal_settled_with_own_balance FROM loans WHERE id = 1")
        ).scalar_one() == 0
    _assert_final_column(legacy_engine)
