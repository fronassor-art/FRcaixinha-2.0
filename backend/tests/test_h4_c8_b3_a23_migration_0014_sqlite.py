import os
import subprocess
import sys

from sqlalchemy import create_engine, text


def _alembic(database, revision):
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


def test_migration_0014_sqlite_removes_only_server_default(tmp_path):
    database = tmp_path / "migration_0014.db"
    _alembic(database, "0013_ledger_hardening_v035")

    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO groups "
                "(name, monthly_amount, months, due_day, active) "
                "VALUES ('legacy-group', 150.00, 12, 10, 1)"
            )
        )

    _alembic(database, "0014_risk_limits_v036")

    with engine.connect() as connection:
        group = connection.execute(
            text("SELECT name, min_cash_reserve FROM groups WHERE name = 'legacy-group'")
        ).one()
        assert group.name == "legacy-group"
        assert str(group.min_cash_reserve) in {"0", "0.00"}

        columns = connection.execute(text("PRAGMA table_info(groups)")).all()
        min_cash_reserve = next(row for row in columns if row[1] == "min_cash_reserve")
        assert min_cash_reserve[2] == "NUMERIC(14, 2)"
        assert min_cash_reserve[3] == 1
        assert min_cash_reserve[4] is None
