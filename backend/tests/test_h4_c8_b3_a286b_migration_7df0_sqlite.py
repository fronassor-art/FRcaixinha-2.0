import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


REVISION = "7df0a77ffd28"


def _upgrade(database):
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{database}",
            "JWT_SECRET": "testsecret",
            "APP_ENV": "test",
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", REVISION],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _insert(engine, statement, parameters):
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(statement, parameters)
            transaction.commit()
            return True, ""
        except IntegrityError as exc:
            transaction.rollback()
            return False, str(exc)


def _unique_columns(connection, table):
    unique_indexes = {}
    for row in connection.execute(text(f"PRAGMA index_list('{table}')")):
        if row[2]:
            unique_indexes[row[1]] = [
                info[2]
                for info in connection.execute(text(f'PRAGMA index_info("{row[1]}")'))
            ]
    return unique_indexes


def test_7df0_sqlite_preserves_pix_storage_and_evidence_version_uniqueness(tmp_path):
    database = tmp_path / "migration_7df0.db"
    _upgrade(database)

    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == REVISION

        contribution_columns = {
            row[1]: row
            for row in connection.execute(text("PRAGMA table_info(contributions)"))
        }
        assert contribution_columns["pix_idempotency_key"][2] == "VARCHAR(64)"
        assert contribution_columns["pix_idempotency_key"][3] == 0

        evidence_file_columns = {
            row[1]: row
            for row in connection.execute(
                text("PRAGMA table_info(workflow_execution_evidence_files)")
            )
        }
        assert evidence_file_columns["storage_key"][2] == "VARCHAR(180)"
        assert evidence_file_columns["storage_key"][3] == 1
        assert evidence_file_columns["evidence_id"][2] == "INTEGER"
        assert evidence_file_columns["evidence_id"][3] == 1
        assert evidence_file_columns["version"][2] == "INTEGER"
        assert evidence_file_columns["version"][3] == 1

        unique_indexes = _unique_columns(connection, "contributions")
        assert ["pix_idempotency_key"] in unique_indexes.values()

        unique_indexes = _unique_columns(connection, "workflow_execution_evidence_files")
        assert ["storage_key"] in unique_indexes.values()
        assert ["evidence_id", "version"] in unique_indexes.values()

        foreign_keys = [
            (row[3], row[2], row[4])
            for row in connection.execute(
                text("PRAGMA foreign_key_list(workflow_execution_evidence_files)")
            )
        ]
        assert ("evidence_id", "workflow_execution_evidence", "id") in foreign_keys
        assert ("uploaded_by", "users", "id") in foreign_keys

        group_sql = text(
            "INSERT INTO groups "
            "(id, name, monthly_amount, months, due_day, active, min_cash_reserve, "
            "max_member_exposure, max_global_exposure, max_exposure_ratio, "
            "max_simultaneous_loans, max_installments, grace_days, min_on_time_ratio, "
            "max_overdue_installments, max_installment_income_ratio, max_quota_multiple, "
            "max_loan_amount, max_loan_income_multiple) "
            "VALUES (1, 'Regression Group', 100, 12, 10, 1, 0, 1000, 10000, 1, "
            "10, 12, 5, 0.5, 3, 0.3, 5, 10000, 10)"
        )
        connection.execute(group_sql)
        for user_id, name, email, cpf in (
            (1, "Regression A", "reg-a@example.invalid", "REG-CPF-A"),
            (2, "Regression B", "reg-b@example.invalid", "REG-CPF-B"),
        ):
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, name, email, cpf, password_hash, role, is_active, created_at) "
                    "VALUES (:id, :name, :email, :cpf, 'hash', 'USER', 1, "
                    "'2099-01-01 00:00:00')"
                ),
                {"id": user_id, "name": name, "email": email, "cpf": cpf},
            )
            connection.execute(
                text(
                    "INSERT INTO members "
                    "(id, user_id, group_id, status, joined_at) "
                    "VALUES (:id, :user_id, 1, 'ACTIVE', '2099-01-01 00:00:00')"
                ),
                {"id": user_id, "user_id": user_id},
            )

    contribution_sql = text(
        "INSERT INTO contributions "
        "(id, member_id, competence, amount, status, created_at, pix_idempotency_key) "
        "VALUES (:id, :member_id, :competence, 10, 'PENDING', "
        "'2099-01-01 00:00:00', :pix_idempotency_key)"
    )
    accepted, _ = _insert(
        engine,
        contribution_sql,
        {"id": 1, "member_id": 1, "competence": "2099-01-01", "pix_idempotency_key": "KEY-A"},
    )
    assert accepted
    accepted, error = _insert(
        engine,
        contribution_sql,
        {"id": 2, "member_id": 2, "competence": "2099-02-01", "pix_idempotency_key": "KEY-A"},
    )
    assert not accepted
    assert "pix_idempotency_key" in error
    accepted, _ = _insert(
        engine,
        contribution_sql,
        {"id": 3, "member_id": 2, "competence": "2099-03-01", "pix_idempotency_key": "KEY-B"},
    )
    assert accepted
    accepted, _ = _insert(
        engine,
        contribution_sql,
        {"id": 4, "member_id": 1, "competence": "2099-04-01", "pix_idempotency_key": None},
    )
    assert accepted
    accepted, _ = _insert(
        engine,
        contribution_sql,
        {"id": 5, "member_id": 2, "competence": "2099-05-01", "pix_idempotency_key": None},
    )
    assert accepted

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO operational_workflow_tasks "
                "(id, action_code, status, priority, created_by, created_at, updated_at) "
                "VALUES (1, 'REGRESSION', 'OPEN', 'NORMAL', 1, "
                "'2099-01-01 00:00:00', '2099-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO workflow_execution_evidence "
                "(id, task_id, added_by, evidence_type, content, content_hash, created_at) "
                "VALUES (1, 1, 1, 'NOTE', 'A', 'HASH-A', '2099-01-01 00:00:00'), "
                "(2, 1, 2, 'NOTE', 'B', 'HASH-B', '2099-01-01 00:00:00')"
            )
        )

    file_sql = text(
        "INSERT INTO workflow_execution_evidence_files "
        "(id, evidence_id, version, original_name, storage_key, content_type, "
        "size_bytes, sha256, uploaded_by, created_at) "
        "VALUES (:id, :evidence_id, :version, :original_name, :storage_key, "
        "'text/plain', 1, :sha256, :uploaded_by, '2099-01-01 00:00:00')"
    )
    accepted, _ = _insert(
        engine,
        file_sql,
        {"id": 1, "evidence_id": 1, "version": 1, "original_name": "a.txt", "storage_key": "key-a", "sha256": "A", "uploaded_by": 1},
    )
    assert accepted

    accepted, error = _insert(
        engine,
        file_sql,
        {"id": 2, "evidence_id": 2, "version": 1, "original_name": "b.txt", "storage_key": "key-a", "sha256": "B", "uploaded_by": 2},
    )
    assert not accepted
    assert "storage_key" in error

    accepted, _ = _insert(
        engine,
        file_sql,
        {"id": 3, "evidence_id": 2, "version": 1, "original_name": "b.txt", "storage_key": "key-b", "sha256": "C", "uploaded_by": 2},
    )
    assert accepted

    accepted, error = _insert(
        engine,
        file_sql,
        {"id": 4, "evidence_id": 1, "version": 1, "original_name": "a-duplicate.txt", "storage_key": "key-c", "sha256": "D", "uploaded_by": 1},
    )
    assert not accepted
    assert "evidence_id" in error
    assert "version" in error

    accepted, _ = _insert(
        engine,
        file_sql,
        {"id": 5, "evidence_id": 1, "version": 2, "original_name": "a-v2.txt", "storage_key": "key-d", "sha256": "E", "uploaded_by": 1},
    )
    assert accepted
