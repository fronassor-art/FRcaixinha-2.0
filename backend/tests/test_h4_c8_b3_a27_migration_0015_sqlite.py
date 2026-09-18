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


def test_migration_0015_sqlite_removes_credit_policy_server_defaults(tmp_path):
    database = tmp_path / "migration_0015.db"
    _alembic(database, "0014_risk_limits_v036")

    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO groups "
                "(name, monthly_amount, months, due_day, active, min_cash_reserve) "
                "VALUES ('legacy-credit-policy-group', 150.00, 12, 10, 1, 0.00)"
            )
        )

    _alembic(database, "0015_credit_policy_v037")

    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0015_credit_policy_v037"
        )
        group = connection.execute(
            text(
                "SELECT max_simultaneous_loans, max_installments, grace_days, "
                "max_overdue_installments FROM groups "
                "WHERE name = 'legacy-credit-policy-group'"
            )
        ).one()
        assert tuple(group) == (1, 12, 0, 0)

        columns = connection.execute(text("PRAGMA table_info(groups)")).all()
        by_name = {row[1]: row for row in columns}
        expected = {
            "max_simultaneous_loans": ("INTEGER", 1),
            "max_installments": ("INTEGER", 1),
            "grace_days": ("INTEGER", 1),
            "max_overdue_installments": ("INTEGER", 1),
        }
        for name, (column_type, not_null) in expected.items():
            row = by_name[name]
            assert row[2] == column_type
            assert row[3] == not_null
            assert row[4] is None
