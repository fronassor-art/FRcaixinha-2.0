import os
import subprocess
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


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


def test_migration_0041_sqlite_preserves_optional_source_task_fk(tmp_path):
    database = tmp_path / "migration_0041.db"
    _alembic(database, "0041_workflow_escalation_v064")

    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0041_workflow_escalation_v064"
        )

        columns = {
            row[1]: row for row in connection.execute(text("PRAGMA table_info(operational_action_records)"))
        }
        assert columns["source_task_id"][2] == "INTEGER"
        assert columns["source_task_id"][3] == 0
        assert columns["source_task_id"][4] is None

        foreign_keys = [
            tuple(row)
            for row in connection.execute(text("PRAGMA foreign_key_list(operational_action_records)"))
        ]
        source_fk = next(row for row in foreign_keys if row[3] == "source_task_id")
        assert source_fk[2] == "operational_workflow_tasks"
        assert source_fk[4] == "id"
        assert source_fk[5] == "NO ACTION"
        assert source_fk[6] == "NO ACTION"

        connection.execute(
            text(
                "INSERT INTO users "
                "(name, email, cpf, password_hash, role, is_active, created_at) "
                "VALUES ('Workflow User', 'workflow@example.test', '99999999999', "
                "'hash', 'ADMIN', 1, '2099-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO operational_workflow_tasks "
                "(action_code, status, priority, created_by, created_at, updated_at) "
                "VALUES ('TEST_TASK', 'PENDING', 'MEDIUM', 1, "
                "'2099-01-01 00:00:00', '2099-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO operational_action_records "
                "(action_code, status, created_at, updated_at, source_task_id) "
                "VALUES ('VALID_SOURCE', 'OPEN', '2099-01-01 00:00:00', "
                "'2099-01-01 00:00:00', 1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO operational_action_records "
                "(action_code, status, created_at, updated_at, source_task_id) "
                "VALUES ('NULL_SOURCE', 'OPEN', '2099-01-01 00:00:00', "
                "'2099-01-01 00:00:00', NULL)"
            )
        )
        try:
            connection.execute(
                text(
                    "INSERT INTO operational_action_records "
                    "(action_code, status, created_at, updated_at, source_task_id) "
                    "VALUES ('INVALID_SOURCE', 'OPEN', '2099-01-01 00:00:00', "
                    "'2099-01-01 00:00:00', 999)"
                )
            )
        except IntegrityError:
            pass
        else:
            raise AssertionError("nonexistent source_task_id was accepted")
