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


def _decision(decision_id, decision_hash):
    return {
        "id": decision_id,
        "status": "PENDING",
        "priority": "MEDIUM",
        "decision_type": "TEST",
        "recommendation": "TEST",
        "decision_hash": decision_hash,
        "created_at": "2099-01-01 00:00:00",
        "updated_at": "2099-01-01 00:00:00",
    }


def _governance(decision_id, integrity_hash):
    return {
        "decision_id": decision_id,
        "required_approvals": 1,
        "approvals_count": 0,
        "status": "PENDING",
        "conflict_status": "CLEAR",
        "conditions_required": 0,
        "validation_status": "PENDING",
        "integrity_hash": integrity_hash,
        "created_at": "2099-01-01 00:00:00",
        "updated_at": "2099-01-01 00:00:00",
    }


def test_migration_0057_sqlite_preserves_execution_constraints_and_indexes(tmp_path):
    database = tmp_path / "migration_0057.db"
    _alembic(database, "0057_executive_risk_execution_v080")

    engine = create_engine(f"sqlite:///{database}")
    decision_sql = text(
        "INSERT INTO executive_risk_decisions "
        "(id, status, priority, decision_type, recommendation, decision_hash, created_at, updated_at) "
        "VALUES (:id, :status, :priority, :decision_type, :recommendation, "
        ":decision_hash, :created_at, :updated_at)"
    )
    governance_sql = text(
        "INSERT INTO executive_risk_decision_governance "
        "(decision_id, required_approvals, approvals_count, status, conflict_status, "
        "conditions_required, validation_status, integrity_hash, created_at, updated_at) "
        "VALUES (:decision_id, :required_approvals, :approvals_count, :status, "
        ":conflict_status, :conditions_required, :validation_status, :integrity_hash, "
        ":created_at, :updated_at)"
    )
    execution_sql = text(
        "INSERT INTO executive_risk_decision_executions "
        "(governance_id, status, execution_hash, created_at, updated_at) "
        "VALUES (:governance_id, :status, :execution_hash, :created_at, :updated_at)"
    )

    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0057_executive_risk_execution_v080"
        )

        columns = {
            row[1]: row
            for row in connection.execute(
                text("PRAGMA table_info(executive_risk_decision_executions)")
            )
        }
        assert columns["governance_id"][2] == "INTEGER"
        assert columns["governance_id"][3] == 1

        ddl = connection.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'executive_risk_decision_executions'"
            )
        ).scalar_one()
        assert "CONSTRAINT uq_exec_risk_execution_governance UNIQUE (governance_id)" in ddl

        foreign_keys = [
            tuple(row)
            for row in connection.execute(
                text("PRAGMA foreign_key_list(executive_risk_decision_executions)")
            )
        ]
        expected_fks = {
            ("governance_id", "executive_risk_decision_governance", "id", False),
            ("assigned_to", "users", "id", True),
            ("started_by", "users", "id", True),
            ("completed_by", "users", "id", True),
            ("verified_by", "users", "id", True),
        }
        assert {
            (row[3], row[2], row[4], row[3] != "governance_id")
            for row in foreign_keys
        } == expected_fks
        assert all(row[5] == "NO ACTION" and row[6] == "NO ACTION" for row in foreign_keys)

        indexes = {
            row[1]: (row[2], [info[2] for info in connection.execute(text(f'PRAGMA index_info("{row[1]}")'))])
            for row in connection.execute(
                text("PRAGMA index_list(executive_risk_decision_executions)")
            )
        }
        assert indexes["ix_exec_risk_exec_status"] == (0, ["status"])
        assert indexes["ix_exec_risk_exec_assigned"] == (0, ["assigned_to"])
        assert indexes["ix_exec_risk_exec_hash"] == (1, ["execution_hash"])
        assert indexes["ix_exec_risk_exec_created"] == (0, ["created_at"])

        connection.execute(decision_sql, _decision(1, "d" * 64))
        connection.execute(decision_sql, _decision(2, "e" * 64))
        connection.execute(governance_sql, _governance(1, "a" * 64))
        connection.execute(governance_sql, _governance(2, "b" * 64))

        connection.execute(
            execution_sql,
            {
                "governance_id": 1,
                "status": "PENDING",
                "execution_hash": "x" * 64,
                "created_at": "2099-01-01 00:00:00",
                "updated_at": "2099-01-01 00:00:00",
            },
        )

        try:
            connection.execute(
                execution_sql,
                {
                    "governance_id": 1,
                    "status": "PENDING",
                    "execution_hash": "y" * 64,
                    "created_at": "2099-01-01 00:00:00",
                    "updated_at": "2099-01-01 00:00:00",
                },
            )
        except IntegrityError as exc:
            assert "governance_id" in str(exc)
        else:
            raise AssertionError("duplicate governance_id was accepted")

        connection.execute(
            execution_sql,
            {
                "governance_id": 2,
                "status": "PENDING",
                "execution_hash": "y" * 64,
                "created_at": "2099-01-01 00:00:00",
                "updated_at": "2099-01-01 00:00:00",
            },
        )
