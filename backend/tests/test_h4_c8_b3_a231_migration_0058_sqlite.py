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


def test_migration_0058_sqlite_preserves_effectiveness_constraints_and_indexes(tmp_path):
    database = tmp_path / "migration_0058.db"
    _alembic(database, "0058_executive_risk_effectiveness_v081")

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
        "(id, governance_id, status, execution_hash, created_at, updated_at) "
        "VALUES (:id, :governance_id, :status, :execution_hash, :created_at, :updated_at)"
    )
    effectiveness_sql = text(
        "INSERT INTO executive_risk_effectiveness "
        "(execution_id, status, indicator_code, effectiveness_criteria, integrity_hash, "
        "created_at, updated_at) VALUES (:execution_id, :status, :indicator_code, "
        ":effectiveness_criteria, :integrity_hash, :created_at, :updated_at)"
    )

    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0058_executive_risk_effectiveness_v081"
        )

        columns = {
            row[1]: row
            for row in connection.execute(
                text("PRAGMA table_info(executive_risk_effectiveness)")
            )
        }
        assert columns["execution_id"][2] == "INTEGER"
        assert columns["execution_id"][3] == 1
        assert columns["reviewed_by"][3] == 0

        ddl = connection.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'executive_risk_effectiveness'"
            )
        ).scalar_one()
        assert "CONSTRAINT uq_exec_risk_effectiveness_execution UNIQUE (execution_id)" in ddl

        foreign_keys = [
            tuple(row)
            for row in connection.execute(
                text("PRAGMA foreign_key_list(executive_risk_effectiveness)")
            )
        ]
        expected_fks = {
            ("execution_id", "executive_risk_decision_executions", "id", False),
            ("reviewed_by", "users", "id", True),
        }
        assert {
            (row[3], row[2], row[4], row[3] == "reviewed_by")
            for row in foreign_keys
        } == expected_fks
        assert all(row[5] == "NO ACTION" and row[6] == "NO ACTION" for row in foreign_keys)

        indexes = {
            row[1]: (row[2], [info[2] for info in connection.execute(text(f'PRAGMA index_info("{row[1]}")'))])
            for row in connection.execute(
                text("PRAGMA index_list(executive_risk_effectiveness)")
            )
        }
        assert indexes["ix_exec_risk_eff_status"] == (0, ["status"])
        assert indexes["ix_exec_risk_eff_hash"] == (1, ["integrity_hash"])
        assert indexes["ix_exec_risk_eff_created"] == (0, ["created_at"])

        connection.execute(decision_sql, _decision(1, "d" * 64))
        connection.execute(decision_sql, _decision(2, "e" * 64))
        connection.execute(governance_sql, _governance(1, "a" * 64))
        connection.execute(governance_sql, _governance(2, "b" * 64))
        connection.execute(
            execution_sql,
            {
                "id": 1,
                "governance_id": 1,
                "status": "PENDING",
                "execution_hash": "x" * 64,
                "created_at": "2099-01-01 00:00:00",
                "updated_at": "2099-01-01 00:00:00",
            },
        )
        connection.execute(
            execution_sql,
            {
                "id": 2,
                "governance_id": 2,
                "status": "PENDING",
                "execution_hash": "y" * 64,
                "created_at": "2099-01-01 00:00:00",
                "updated_at": "2099-01-01 00:00:00",
            },
        )

        connection.execute(
            effectiveness_sql,
            {
                "execution_id": 1,
                "status": "PENDING",
                "indicator_code": "TEST_IND",
                "effectiveness_criteria": "criteria",
                "integrity_hash": "m" * 64,
                "created_at": "2099-01-01 00:00:00",
                "updated_at": "2099-01-01 00:00:00",
            },
        )

        try:
            connection.execute(
                effectiveness_sql,
                {
                    "execution_id": 1,
                    "status": "PENDING",
                    "indicator_code": "TEST_IND",
                    "effectiveness_criteria": "criteria",
                    "integrity_hash": "n" * 64,
                    "created_at": "2099-01-01 00:00:00",
                    "updated_at": "2099-01-01 00:00:00",
                },
            )
        except IntegrityError as exc:
            assert "execution_id" in str(exc)
        else:
            raise AssertionError("duplicate execution_id was accepted")

        connection.execute(
            effectiveness_sql,
            {
                "execution_id": 2,
                "status": "PENDING",
                "indicator_code": "TEST_IND",
                "effectiveness_criteria": "criteria",
                "integrity_hash": "n" * 64,
                "created_at": "2099-01-01 00:00:00",
                "updated_at": "2099-01-01 00:00:00",
            },
        )
