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


def _create_valid_task_and_orchestration(connection, task_id):
    connection.execute(
        text(
            "INSERT INTO users "
            "(name, email, cpf, password_hash, role, is_active, created_at) "
            "VALUES (:name, :email, :cpf, 'hash', 'ADMIN', 1, "
            "'2099-01-01 00:00:00')"
        ),
        {
            "name": f"Workflow User {task_id}",
            "email": f"workflow{task_id}@example.test",
            "cpf": f"{task_id:011d}",
        },
    )
    connection.execute(
        text(
            "INSERT INTO operational_workflow_tasks "
            "(action_code, status, priority, created_by, created_at, updated_at) "
            "VALUES ('TEST_TASK', 'PENDING', 'MEDIUM', :user_id, "
            "'2099-01-01 00:00:00', '2099-01-01 00:00:00')"
        ),
        {"user_id": task_id},
    )
    connection.execute(
        text(
            "INSERT INTO operational_workflow_orchestrations "
            "(task_id, queue_status, priority, sla_status, escalation_level, "
            "orchestration_score, last_evaluated_at, created_at, updated_at) "
            "VALUES (:task_id, 'READY', 'MEDIUM', 'ON_TRACK', 'NONE', 0, "
            "'2099-01-01 00:00:00', '2099-01-01 00:00:00', "
            "'2099-01-01 00:00:00')"
        ),
        {"task_id": task_id},
    )


def test_migration_0043_sqlite_preserves_three_audit_fks(tmp_path):
    database = tmp_path / "migration_0043.db"
    _alembic(database, "0043_execution_assist_v066")

    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0043_execution_assist_v066"
        )

        columns = {
            row[1]: row
            for row in connection.execute(
                text("PRAGMA table_info(operational_workflow_orchestrations)")
            )
        }
        for name in ("accepted_by", "started_by", "completed_by"):
            assert columns[name][2] == "INTEGER"
            assert columns[name][3] == 0
            assert columns[name][4] is None

        foreign_keys = [
            tuple(row)
            for row in connection.execute(
                text("PRAGMA foreign_key_list(operational_workflow_orchestrations)")
            )
        ]
        for name in ("accepted_by", "started_by", "completed_by"):
            fk = next(row for row in foreign_keys if row[3] == name)
            assert fk[2] == "users"
            assert fk[4] == "id"
            assert fk[5] == "NO ACTION"
            assert fk[6] == "NO ACTION"

        for name, task_id in (("accepted_by", 1), ("started_by", 2), ("completed_by", 3)):
            _create_valid_task_and_orchestration(connection, task_id)
            connection.execute(
                text(
                    f"UPDATE operational_workflow_orchestrations SET {name} = 1 "
                    "WHERE task_id = :task_id"
                ),
                {"task_id": task_id},
            )

        _create_valid_task_and_orchestration(connection, 4)

    for name, task_id in (("accepted_by", 5), ("started_by", 6), ("completed_by", 7)):
        with engine.begin() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            _create_valid_task_and_orchestration(connection, task_id)
            try:
                connection.execute(
                    text(
                        f"UPDATE operational_workflow_orchestrations SET {name} = 999 "
                        "WHERE task_id = :task_id"
                    ),
                    {"task_id": task_id},
                )
            except IntegrityError:
                pass
            else:
                raise AssertionError(f"invalid {name} reference was accepted")
