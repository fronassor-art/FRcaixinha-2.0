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


def test_migration_0065_sqlite_preserves_ci_execution_constraints_and_indexes(tmp_path):
    database = tmp_path / "migration_0065.db"
    _alembic(database, "0065_continuous_improvement_execution_v088")

    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0065_continuous_improvement_execution_v088"
        )

        columns = {
            row[1]: row
            for row in connection.execute(
                text("PRAGMA table_info(continuous_improvement_executions)")
            )
        }
        assert columns["decision_id"][2] == "INTEGER"
        assert columns["decision_id"][3] == 1
        assert columns["recommendation_id"][3] == 1
        assert columns["plan_id"][3] == 1
        assert columns["assigned_to"][3] == 1
        assert columns["verified_by"][3] == 0

        ddl = connection.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'continuous_improvement_executions'"
            )
        ).scalar_one()
        assert "CONSTRAINT uq_ci_exec_decision UNIQUE (decision_id)" in ddl

        foreign_keys = [
            tuple(row)
            for row in connection.execute(
                text("PRAGMA foreign_key_list(continuous_improvement_executions)")
            )
        ]
        expected_fks = {
            ("decision_id", "continuous_improvement_assignment_decisions", "id", False),
            ("recommendation_id", "continuous_improvement_recommendations", "id", False),
            ("plan_id", "continuous_improvement_plans", "id", False),
            ("assigned_to", "users", "id", False),
            ("verified_by", "users", "id", True),
        }
        assert len(foreign_keys) == 5
        assert {
            (row[3], row[2], row[4], row[3] == "verified_by")
            for row in foreign_keys
        } == expected_fks
        assert all(row[5] == "NO ACTION" and row[6] == "NO ACTION" for row in foreign_keys)

        indexes = {
            row[1]: (row[2], [info[2] for info in connection.execute(text(f'PRAGMA index_info("{row[1]}")'))])
            for row in connection.execute(
                text("PRAGMA index_list(continuous_improvement_executions)")
            )
            if not row[1].startswith("sqlite_autoindex_")
        }
        assert len(indexes) == 9
        assert indexes["ix_ci_exec_decision"] == (0, ["decision_id"])
        assert indexes["ix_ci_exec_rec"] == (0, ["recommendation_id"])
        assert indexes["ix_ci_exec_plan"] == (0, ["plan_id"])
        assert indexes["ix_ci_exec_status"] == (0, ["status"])
        assert indexes["ix_ci_exec_assigned"] == (0, ["assigned_to"])
        assert indexes["ix_ci_exec_verified"] == (0, ["verified_by"])
        assert indexes["ix_ci_exec_hash"] == (1, ["execution_hash"])
        assert indexes["ix_ci_exec_created"] == (0, ["created_at"])
        assert indexes["ix_ci_exec_updated"] == (0, ["updated_at"])

        connection.execute(
            text(
                "INSERT INTO users (id, name, email, cpf, password_hash, role, is_active, created_at) "
                "VALUES (1, 'CI User', 'ci@example.test', '11111111111', 'hash', 'ADMIN', 1, "
                "'2099-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO continuous_improvement_assignment_snapshots "
                "(id, snapshot_date, status, snapshot_json, snapshot_hash, generated_by, created_at, updated_at) "
                "VALUES (1, '2099-01-01', 'PASS', '{}', :hash, 1, '2099-01-01 00:00:00', "
                "'2099-01-01 00:00:00')"
            ),
            {"hash": "s" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO continuous_improvement_recommendations "
                "(id, indicator_code, pattern_code, status, sample_size, effective_count, partial_count, "
                "ineffective_count, recommendation, integrity_hash, created_at, updated_at) "
                "VALUES (1, 'TEST_IND', 'TEST_PATTERN', 'OPEN', 1, 0, 0, 0, 'TEST', :hash, "
                "'2099-01-01 00:00:00', '2099-01-01 00:00:00')"
            ),
            {"hash": "r" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO continuous_improvement_plans "
                "(id, recommendation_id, status, indicator_code, target_direction, objective, integrity_hash, "
                "created_at, updated_at) VALUES (1, 1, 'OPEN', 'TEST_IND', 'INCREASE', 'TEST', :hash, "
                "'2099-01-01 00:00:00', '2099-01-01 00:00:00')"
            ),
            {"hash": "p" * 64},
        )
        decision_sql = text(
            "INSERT INTO continuous_improvement_assignment_decisions "
            "(id, snapshot_id, recommendation_id, decision, decision_note, decided_by, decided_at, "
            "decision_hash, created_at, updated_at) VALUES (:id, 1, 1, 'APPROVED', 'TEST', 1, "
            "'2099-01-01 00:00:00', :hash, '2099-01-01 00:00:00', '2099-01-01 00:00:00')"
        )
        connection.execute(decision_sql, {"id": 1, "hash": "d" * 64})
        connection.execute(decision_sql, {"id": 2, "hash": "e" * 64})

        execution_sql = text(
            "INSERT INTO continuous_improvement_executions "
            "(decision_id, recommendation_id, plan_id, status, assigned_to, execution_hash, created_at, updated_at) "
            "VALUES (:decision_id, 1, 1, 'PENDING', 1, :hash, '2099-01-01 00:00:00', "
            "'2099-01-01 00:00:00')"
        )
        connection.execute(execution_sql, {"decision_id": 1, "hash": "x" * 64})

        try:
            connection.execute(execution_sql, {"decision_id": 1, "hash": "y" * 64})
        except IntegrityError as exc:
            assert "decision_id" in str(exc)
        else:
            raise AssertionError("duplicate decision_id was accepted")

        connection.execute(execution_sql, {"decision_id": 2, "hash": "y" * 64})
