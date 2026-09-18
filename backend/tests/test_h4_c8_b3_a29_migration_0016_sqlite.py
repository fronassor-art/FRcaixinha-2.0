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


def test_migration_0016_sqlite_preserves_dunning_schema_and_data(tmp_path):
    database = tmp_path / "migration_0016.db"
    _alembic(database, "0015_credit_policy_v037")

    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(name, email, cpf, password_hash, role, is_active, created_at) "
                "VALUES ('Legacy User', 'legacy@example.test', '00000000000', "
                "'hash', 'MEMBER', 1, '2099-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO groups "
                "(name, monthly_amount, months, due_day, active, min_cash_reserve, "
                "max_simultaneous_loans, max_installments, grace_days, "
                "max_overdue_installments) "
                "VALUES ('Legacy Group', 150.00, 12, 10, 1, 0.00, 1, 12, 0, 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO members (user_id, group_id, status, joined_at) "
                "VALUES (1, 1, 'ACTIVE', '2099-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO loans "
                "(member_id, principal, monthly_rate, installments, status, requested_at) "
                "VALUES (1, 100.00, 0.20, 1, 'APPROVED', '2099-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO loan_installments "
                "(loan_id, number, due_date, principal, interest, amount, paid_amount, "
                "status, penalty_amount, paid_penalty_amount, paid_at) "
                "VALUES (1, 1, '2099-02-01', 100.00, 0.00, 100.00, 0.00, "
                "'PENDING', 0.00, 0.00, NULL)"
            )
        )

    _alembic(database, "0016_collection_dunning_v038")

    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0016_collection_dunning_v038"
        )
        installment = connection.execute(
            text(
                "SELECT id, collection_stage, collection_attempts "
                "FROM loan_installments WHERE id = 1"
            )
        ).one()
        assert tuple(installment) == (1, "NORMAL", 0)

        installment_columns = connection.execute(text("PRAGMA table_info(loan_installments)")).all()
        by_name = {row[1]: row for row in installment_columns}
        assert by_name["collection_stage"][2] == "VARCHAR(20)"
        assert by_name["collection_stage"][3] == 1
        assert by_name["collection_stage"][4] is None
        assert by_name["collection_attempts"][2] == "INTEGER"
        assert by_name["collection_attempts"][3] == 1
        assert by_name["collection_attempts"][4] is None
        assert by_name["last_collection_at"][2] == "DATETIME"
        assert by_name["last_collection_at"][3] == 0

        indexes = {row[1] for row in connection.execute(text("PRAGMA index_list(loan_installments)"))}
        assert "ix_installments_collection_stage" in indexes

        event_columns = {
            row[1]: row for row in connection.execute(text("PRAGMA table_info(collection_events)"))
        }
        assert set(event_columns) == {
            "id",
            "installment_id",
            "member_id",
            "event_type",
            "event_date",
            "channel",
            "notification_id",
            "created_at",
        }
        assert connection.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'collection_events'"
            )
        ).scalar_one() == "collection_events"
