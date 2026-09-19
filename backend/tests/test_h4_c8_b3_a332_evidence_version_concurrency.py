import hashlib
import io
import os
import sqlite3
import subprocess
import sys
import threading
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import UploadFile
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import Headers

from app.core.config import settings
from app.models import (
    AuditLog,
    OperationalWorkflowOrchestration,
    OperationalWorkflowTask,
    User,
    WorkflowExecutionEvidence,
    WorkflowExecutionEvidenceFile,
)
from app.services import workflow_evidence_storage_v068 as storage
from app.services.workflow_evidence_storage_v068 import upload_file
from app.api import admin_workflow_storage_v068 as storage_api


BASE_REVISION = "0089_collection_agreement_subjects"
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _upgrade(database):
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{database}",
            "JWT_SECRET": "testsecret",
            "APP_ENV": "test",
        }
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", BASE_REVISION],
        cwd=BACKEND_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _engine(database):
    engine = create_engine(
        f"sqlite:///{database}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _upload(payload, filename):
    return UploadFile(
        file=io.BytesIO(payload),
        filename=filename,
        headers=Headers({"content-type": "text/plain"}),
    )


def _seed(engine):
    sessions = sessionmaker(bind=engine)
    db = sessions()
    user = User(
        name="Evidence concurrency",
        email="evidence-concurrency@example.invalid",
        cpf="evidence-concurrency-cpf",
        password_hash="hash",
        role="ADMIN",
    )
    task = OperationalWorkflowTask(
        action_code="EVIDENCE_CONCURRENCY",
        status="OPEN",
        priority="MEDIUM",
        created_by=1,
    )
    db.add(user)
    db.flush()
    task.created_by = user.id
    db.add(task)
    db.flush()
    orchestration = OperationalWorkflowOrchestration(
        task_id=task.id,
        priority="MEDIUM",
        sla_status="ON_TRACK",
        execution_state="IN_EXECUTION",
        started_by=user.id,
    )
    evidence = WorkflowExecutionEvidence(
        task_id=task.id,
        added_by=user.id,
        evidence_type="ATTACHMENT",
        title="Concurrency evidence",
        content="attachment",
        content_hash=hashlib.sha256(b"attachment").hexdigest(),
    )
    db.add_all([orchestration, evidence])
    db.commit()
    ids = {"task": task.id, "evidence": evidence.id, "actor": user.id}
    db.close()
    return sessions, ids


def _physical_keys(root):
    return sorted(
        str(path.relative_to(root)).replace(os.sep, "/")
        for path in root.rglob("*")
        if path.is_file()
    )


def _run_race(engine, root, ids):
    sessions = sessionmaker(bind=engine)
    barrier = threading.Barrier(2)
    results = []
    result_lock = threading.Lock()
    observed = []
    observed_lock = threading.Lock()
    first_observations = 0
    original_next_version = storage._next_version

    def controlled_next_version(db, evidence_id):
        nonlocal first_observations
        version = original_next_version(db, evidence_id)
        with observed_lock:
            observed.append(version)
            first_observations += 1
            synchronize = first_observations <= 2
        if synchronize:
            barrier.wait(timeout=30)
        return version

    storage._next_version = controlled_next_version

    def worker(name, payload):
        db = sessions()
        row = {"name": name}
        try:
            task = db.get(OperationalWorkflowTask, ids["task"])
            evidence = db.get(WorkflowExecutionEvidence, ids["evidence"])
            result = upload_file(db, task, evidence, ids["actor"], _upload(payload, f"{name}.txt"))
            row["storage_key"] = result.storage_key
            row["version"] = result.version
            row["flush"] = "OK"
            db.commit()
            row["commit"] = "OK"
        except Exception as exc:
            row["exception"] = type(exc).__name__
            row["message"] = str(exc)
            row["rollback_required"] = True
            try:
                db.query(WorkflowExecutionEvidenceFile).count()
            except Exception as before_rollback:
                row["before_rollback"] = type(before_rollback).__name__
            try:
                db.rollback()
                row["rollback"] = "OK"
                row["after_rollback"] = db.query(WorkflowExecutionEvidenceFile).count()
            except Exception as rollback_exc:
                row["rollback"] = type(rollback_exc).__name__
        finally:
            db.close()
            with result_lock:
                results.append(row)

    try:
        threads = [
            threading.Thread(target=worker, args=("a", b"evidence-a")),
            threading.Thread(target=worker, args=("b", b"evidence-b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert all(not thread.is_alive() for thread in threads)
    finally:
        storage._next_version = original_next_version
    return observed, results


def _snapshot(engine, evidence_id):
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT version, storage_key FROM workflow_execution_evidence_files "
                "WHERE evidence_id = :evidence_id ORDER BY id"
            ),
            {"evidence_id": evidence_id},
        ).all()
        audit_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM audit_logs WHERE action = 'WORKFLOW_EVIDENCE_FILE_UPLOADED'"
            )
        ).scalar_one()
    return rows, audit_count


def _configure_storage(monkeypatch, root):
    monkeypatch.setattr(settings, "workflow_evidence_storage_root", str(root))
    monkeypatch.setattr(settings, "workflow_evidence_max_bytes", 1024 * 1024)
    monkeypatch.setattr(settings, "workflow_evidence_allowed_types", "text/plain")


def _insert_file(engine, evidence_id, actor_id, version, storage_key):
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO workflow_execution_evidence_files "
                "(evidence_id, version, original_name, storage_key, content_type, "
                "size_bytes, sha256, uploaded_by, created_at) "
                "VALUES (:evidence_id, :version, 'existing.txt', :storage_key, "
                "'text/plain', 8, 'existing', :actor_id, CURRENT_TIMESTAMP)"
            ),
            {
                "evidence_id": evidence_id,
                "version": version,
                "storage_key": storage_key,
                "actor_id": actor_id,
            },
        )


def _sqlite_integrity_error(message):
    return IntegrityError("INSERT", {}, sqlite3.IntegrityError(message))


def test_concurrent_uploads_observe_same_version_and_preserve_unique_constraint(tmp_path, monkeypatch):
    database = tmp_path / "evidence_concurrency.db"
    root = tmp_path / "storage"
    root.mkdir()
    _upgrade(database)
    engine = _engine(database)
    _sessions, ids = _seed(engine)
    _configure_storage(monkeypatch, root)

    before = _physical_keys(root)
    observed, results = _run_race(engine, root, ids)
    rows, audit_count = _snapshot(engine, ids["evidence"])
    after = _physical_keys(root)

    assert len(observed) >= 2
    assert observed[0] == observed[1] == 1
    assert len(results) == 2
    assert sum(row.get("commit") == "OK" for row in results) == 2
    assert sum("exception" in row for row in results) == 0
    assert len(rows) == 2
    assert [row.version for row in rows] == [1, 2]
    assert len({row.version for row in rows}) == len(rows)
    assert len({row.storage_key for row in rows}) == len(rows)
    assert len(after) == 2
    assert len(after) - len(rows) == 0
    assert audit_count == len(rows)
    assert before == []


def test_serial_uploads_increment_versions_without_orphans(tmp_path, monkeypatch):
    database = tmp_path / "evidence_serial.db"
    root = tmp_path / "storage"
    root.mkdir()
    _upgrade(database)
    engine = _engine(database)
    sessions, ids = _seed(engine)
    _configure_storage(monkeypatch, root)

    db = sessions()
    task = db.get(OperationalWorkflowTask, ids["task"])
    evidence = db.get(WorkflowExecutionEvidence, ids["evidence"])
    first = upload_file(db, task, evidence, ids["actor"], _upload(b"serial-a", "a.txt"))
    first_version, first_storage_key = first.version, first.storage_key
    db.commit()
    second = upload_file(db, task, evidence, ids["actor"], _upload(b"serial-b", "b.txt"))
    second_version, second_storage_key = second.version, second.storage_key
    db.commit()
    db.close()

    rows, audit_count = _snapshot(engine, ids["evidence"])
    assert (first_version, second_version) == (1, 2)
    assert [row.version for row in rows] == [1, 2]
    assert first_storage_key != second_storage_key
    assert len(_physical_keys(root)) == 2
    assert audit_count == 2


def test_schema_rejects_direct_duplicate_evidence_version(tmp_path):
    database = tmp_path / "evidence_unique.db"
    _upgrade(database)
    engine = _engine(database)
    sessions, ids = _seed(engine)
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(
            text(
                "INSERT INTO workflow_execution_evidence_files "
                "(evidence_id, version, original_name, storage_key, content_type, size_bytes, sha256, uploaded_by, created_at) "
                "VALUES (:evidence_id, 1, 'a.txt', 'key-a', 'text/plain', 1, 'a', :actor_id, CURRENT_TIMESTAMP)"
            ),
            {"evidence_id": ids["evidence"], "actor_id": ids["actor"]},
        )
        transaction.commit()

    with engine.connect() as connection:
        transaction = connection.begin()
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO workflow_execution_evidence_files "
                    "(evidence_id, version, original_name, storage_key, content_type, size_bytes, sha256, uploaded_by, created_at) "
                    "VALUES (:evidence_id, 1, 'b.txt', 'key-b', 'text/plain', 1, 'b', :actor_id, CURRENT_TIMESTAMP)"
                ),
                {"evidence_id": ids["evidence"], "actor_id": ids["actor"]},
            )
        transaction.rollback()


def test_retry_preserves_outer_transaction_and_persists_next_version(tmp_path, monkeypatch):
    database = tmp_path / "evidence_outer_transaction.db"
    root = tmp_path / "storage"
    root.mkdir()
    _upgrade(database)
    engine = _engine(database)
    sessions, ids = _seed(engine)
    _configure_storage(monkeypatch, root)
    (root / str(ids["task"])).mkdir()
    (root / str(ids["task"]) / "existing.bin").write_bytes(b"existing")
    _insert_file(engine, ids["evidence"], ids["actor"], 1, f'{ids["task"]}/existing.bin')

    db = sessions()
    task = db.get(OperationalWorkflowTask, ids["task"])
    evidence = db.get(WorkflowExecutionEvidence, ids["evidence"])
    task.priority = "HIGH"
    uploaded = upload_file(db, task, evidence, ids["actor"], _upload(b"retry", "retry.txt"))
    expected_version = uploaded.version
    db.commit()
    db.close()

    check = sessions()
    assert check.get(OperationalWorkflowTask, ids["task"]).priority == "HIGH"
    assert check.query(WorkflowExecutionEvidenceFile).filter_by(evidence_id=ids["evidence"]).count() == 2
    assert expected_version == 2
    check.close()


def test_unrelated_integrity_errors_are_not_classified_as_version_conflicts():
    assert not storage._is_version_conflict(
        _sqlite_integrity_error("UNIQUE constraint failed: workflow_execution_evidence_files.storage_key")
    )
    assert not storage._is_version_conflict(
        _sqlite_integrity_error("FOREIGN KEY constraint failed")
    )
    assert not storage._is_version_conflict(
        _sqlite_integrity_error("CHECK constraint failed: example")
    )


def test_postgresql_classifier_requires_sqlstate_and_constraint():
    original = SimpleNamespace(
        sqlstate="23505",
        diag=SimpleNamespace(constraint_name="uq_workflow_evidence_file_version"),
    )
    assert storage._is_version_conflict(IntegrityError("INSERT", {}, original))
    assert not storage._is_version_conflict(
        IntegrityError(
            "INSERT",
            {},
            SimpleNamespace(sqlstate="23505", diag=SimpleNamespace(constraint_name="other")),
        )
    )
    assert not storage._is_version_conflict(
        IntegrityError(
            "INSERT",
            {},
            SimpleNamespace(sqlstate="23501", diag=SimpleNamespace(constraint_name=storage.VERSION_CONSTRAINT)),
        )
    )


def test_orm_declares_named_composite_unique():
    constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in WorkflowExecutionEvidenceFile.__table__.constraints
        if constraint.name
    }
    assert constraints["uq_workflow_evidence_file_version"] == ("evidence_id", "version")


def test_endpoint_commit_failure_cleans_only_its_storage_key(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    root.mkdir()
    _configure_storage(monkeypatch, root)
    key = "7/commit-failure.bin"
    path = root / key
    path.parent.mkdir()
    path.write_bytes(b"pending")

    row = SimpleNamespace(
        id=1,
        evidence_id=2,
        version=1,
        original_name="commit.txt",
        content_type="text/plain",
        size_bytes=7,
        sha256="hash",
        storage_key=key,
    )

    class FakeDB:
        def get(self, model, identifier):
            return SimpleNamespace(id=identifier)

        def commit(self):
            raise RuntimeError("commit failed")

        def rollback(self):
            self.rolled_back = True

    db = FakeDB()
    monkeypatch.setattr(storage_api, "upload_file", lambda *args, **kwargs: row)
    with pytest.raises(RuntimeError, match="commit failed"):
        storage_api.upload_attachment(
            7,
            2,
            file=_upload(b"pending", "commit.txt"),
            admin=SimpleNamespace(id=3),
            db=db,
        )
    assert db.rolled_back is True
    assert not path.exists()


def test_retry_limit_is_bounded_and_cleans_file(tmp_path, monkeypatch):
    database = tmp_path / "evidence_retry_limit.db"
    root = tmp_path / "storage"
    root.mkdir()
    _upgrade(database)
    engine = _engine(database)
    sessions, ids = _seed(engine)
    _configure_storage(monkeypatch, root)

    monkeypatch.setattr(storage, "_is_version_conflict", lambda exc: True)
    (root / str(ids["task"])).mkdir()
    (root / str(ids["task"]) / "existing.bin").write_bytes(b"existing")
    _insert_file(engine, ids["evidence"], ids["actor"], 1, f'{ids["task"]}/existing.bin')
    db = sessions()
    task = db.get(OperationalWorkflowTask, ids["task"])
    evidence = db.get(WorkflowExecutionEvidence, ids["evidence"])
    version_calls = 0

    def always_version(db, evidence_id):
        nonlocal version_calls
        version_calls += 1
        return 1

    monkeypatch.setattr(storage, "_next_version", always_version)
    with pytest.raises(IntegrityError):
        upload_file(db, task, evidence, ids["actor"], _upload(b"limit", "limit.txt"))
    assert version_calls == storage.VERSION_ATTEMPTS
    assert _physical_keys(root) == [f'{ids["task"]}/existing.bin']
    db.rollback()
    db.close()
