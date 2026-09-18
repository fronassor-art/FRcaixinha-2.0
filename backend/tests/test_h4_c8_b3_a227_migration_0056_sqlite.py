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


def test_migration_0056_sqlite_preserves_governance_constraints_and_indexes(tmp_path):
    database = tmp_path / "migration_0056.db"
    _alembic(database, "0056_executive_risk_governance_v079")

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

    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0056_executive_risk_governance_v079"
        )

        columns = {
            row[1]: row
            for row in connection.execute(
                text("PRAGMA table_info(executive_risk_decision_governance)")
            )
        }
        assert columns["decision_id"][2] == "INTEGER"
        assert columns["decision_id"][3] == 1
        assert columns["primary_approver_id"][3] == 0
        assert columns["secondary_approver_id"][3] == 0
        assert columns["validated_by"][3] == 0

        ddl = connection.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'executive_risk_decision_governance'"
            )
        ).scalar_one()
        assert "CONSTRAINT uq_exec_risk_gov_decision UNIQUE (decision_id)" in ddl

        foreign_keys = [
            tuple(row)
            for row in connection.execute(
                text("PRAGMA foreign_key_list(executive_risk_decision_governance)")
            )
        ]
        expected_fks = {
            ("decision_id", "executive_risk_decisions", "id", False),
            ("primary_approver_id", "users", "id", True),
            ("secondary_approver_id", "users", "id", True),
            ("validated_by", "users", "id", True),
        }
        assert {
            (row[3], row[2], row[4], row[3] != "decision_id")
            for row in foreign_keys
        } == expected_fks
        assert all(row[5] == "NO ACTION" and row[6] == "NO ACTION" for row in foreign_keys)

        indexes = {
            row[1]: (row[2], [info[2] for info in connection.execute(text(f'PRAGMA index_info("{row[1]}")'))])
            for row in connection.execute(
                text("PRAGMA index_list(executive_risk_decision_governance)")
            )
        }
        assert indexes["ix_exec_risk_gov_status"] == (0, ["status"])
        assert indexes["ix_exec_risk_gov_validation"] == (0, ["validation_status"])
        assert indexes["ix_exec_risk_gov_hash"] == (1, ["integrity_hash"])

        connection.execute(decision_sql, _decision(1, "d" * 64))
        connection.execute(decision_sql, _decision(2, "e" * 64))
        connection.execute(governance_sql, _governance(1, "a" * 64))

        try:
            connection.execute(governance_sql, _governance(1, "b" * 64))
        except IntegrityError as exc:
            assert "decision_id" in str(exc)
        else:
            raise AssertionError("duplicate governance decision_id was accepted")

        connection.execute(governance_sql, _governance(2, "b" * 64))
