"""Read-only evidence inspection versus actor-authorized verification."""

import hashlib
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import (
    AuditLog,
    ContinuousImprovementAuditSnapshot,
    ContinuousImprovementEvidenceIntegrityEvent,
    ContinuousImprovementExecution,
    ContinuousImprovementExecutionEvidenceFile,
    ContinuousImprovementExecutiveAuditSnapshot,
    SchedulerRun,
    User,
)
from app.services import continuous_improvement_evidence_v089 as evidence
from app.services.continuous_improvement_audit_v091 import (
    build_cycle, persist as persist_cycle_audit, verify_snapshot,
)
from app.services.continuous_improvement_executive_audit_v092 import (
    build_report, persist_report, verify_report,
)
from app.worker import tasks


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(evidence, "storage_path", lambda key: tmp_path / key)
    try:
        with factory() as session:
            yield session, tmp_path
    finally:
        engine.dispose()


def _execution(db, tmp_path, *, with_file=True):
    admin = User(
        name="Evidence Admin", email="evidence-admin@example.test",
        cpf="11122233344", password_hash="test-only", role="ADMIN", is_active=True,
    )
    db.add(admin)
    db.flush()
    execution = ContinuousImprovementExecution(
        decision_id=1, recommendation_id=1, plan_id=1, status="VERIFIED",
        assigned_to=admin.id, verified_by=admin.id,
        execution_hash=uuid.uuid4().hex,
    )
    db.add(execution)
    db.flush()
    if with_file:
        content = b"evidence for the executive report"
        (tmp_path / "evidence.bin").write_bytes(content)
        row = ContinuousImprovementExecutionEvidenceFile(
            execution_id=execution.id, version=1, original_name="evidence.txt",
            storage_key="evidence.bin", content_type="text/plain",
            size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest(),
            uploaded_by=admin.id,
        )
        db.add(row)
        db.flush()
        execution.evidence_manifest_hash = evidence.digest([{
            "id": row.id, "version": row.version, "original_name": row.original_name,
            "size_bytes": row.size_bytes, "sha256": row.sha256,
        }])
    db.commit()
    return execution, admin


def _event_count(db):
    return db.query(ContinuousImprovementEvidenceIntegrityEvent).count()


def test_executive_report_with_no_executions_is_read_only(db):
    session, _ = db
    report = build_report(session)
    assert report["indicators"]["executions"] == 0
    assert _event_count(session) == 0
    assert not session.new and not session.dirty


def test_executive_report_inspects_existing_execution_without_actor_or_writes(db):
    session, tmp_path = db
    execution, _ = _execution(session, tmp_path)

    report = build_report(session)

    assert report["indicators"]["executions"] == 1
    assert report["cycles"][0]["execution_id"] == execution.id
    assert report["cycles"][0]["checks"]["evidence_integrity"] is True
    assert _event_count(session) == 0
    assert not session.new and not session.dirty


def test_cycle_builder_inspects_evidence_without_creating_events(db):
    session, tmp_path = db
    execution, _ = _execution(session, tmp_path)
    result = build_cycle(session, execution.id)
    assert result["evidence_check"]["valid"] is True
    assert _event_count(session) == 0
    assert not session.new and not session.dirty


def test_internal_inspection_detects_bad_file_without_writes(db):
    session, tmp_path = db
    execution, _ = _execution(session, tmp_path)
    (tmp_path / "evidence.bin").write_bytes(b"altered")
    result = evidence.inspect_execution_evidence(session, execution.id)
    assert result["valid"] is False
    assert result["counts"]["MISMATCH"] == 1
    assert _event_count(session) == 0
    assert not session.new and not session.dirty


def test_internal_inspection_reports_missing_evidence_without_writes(db):
    session, tmp_path = db
    execution, admin = _execution(session, tmp_path, with_file=False)
    result = evidence.inspect_execution_evidence(session, execution.id)
    assert result["valid"] is False
    assert result["checked"] == 0
    with pytest.raises(ValueError, match="evidence_file_required"):
        evidence.verify_execution_evidence(session, execution.id, admin.id)
    assert _event_count(session) == 0


def test_executive_report_marks_execution_without_evidence_invalid(db):
    session, tmp_path = db
    _execution(session, tmp_path, with_file=False)
    report = build_report(session)
    assert report["indicators"]["executions"] == 1
    assert report["indicators"]["integrity_failures"] == 1
    assert report["cycles"][0]["checks"]["evidence_integrity"] is False
    assert _event_count(session) == 0


def test_audited_verification_rejects_missing_and_ineligible_actor(db):
    session, tmp_path = db
    execution, admin = _execution(session, tmp_path)
    with pytest.raises(ValueError, match="actor_not_eligible"):
        evidence.verify_execution_evidence(session, execution.id, None)
    admin.is_active = False
    session.flush()
    with pytest.raises(ValueError, match="actor_not_eligible"):
        evidence.verify_execution_evidence(session, execution.id, admin.id)
    assert _event_count(session) == 0


def test_audited_verification_requires_admin_and_records_event(db):
    session, tmp_path = db
    execution, admin = _execution(session, tmp_path)
    result = evidence.verify_execution_evidence(session, execution.id, admin.id)
    assert result["valid"] is True
    events = session.query(ContinuousImprovementEvidenceIntegrityEvent).all()
    assert len(events) == 1
    assert events[0].actor_id == admin.id
    assert events[0].status == "PASS"


def test_audited_verification_records_invalid_file_result(db):
    session, tmp_path = db
    execution, admin = _execution(session, tmp_path)
    (tmp_path / "evidence.bin").write_bytes(b"altered")
    result = evidence.verify_execution_evidence(session, execution.id, admin.id)
    assert result["valid"] is False
    events = session.query(ContinuousImprovementEvidenceIntegrityEvent).all()
    assert len(events) == 1 and events[0].status == "MISMATCH"
    assert events[0].actor_id == admin.id


def test_missing_execution_is_rejected_by_both_paths(db):
    session, _ = db
    with pytest.raises(ValueError, match="execution_not_found"):
        evidence.inspect_execution_evidence(session, 999)
    with pytest.raises(ValueError, match="execution_not_found"):
        evidence.verify_execution_evidence(session, 999, None)
    assert _event_count(session) == 0


def test_daily_with_execution_commits_report_and_run_together(db, monkeypatch):
    session, tmp_path = db
    _execution(session, tmp_path)
    monkeypatch.setattr(
        tasks, "SessionLocal",
        sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False),
    )

    def report_effect(task_db, financial_date):
        report, _ = persist_report(task_db, None)
        task_db.add(AuditLog(
            action="P4F1_DAILY_EFFECT", entity_type="scheduler_test",
            entity_id=financial_date.isoformat(), details="transaction sentinel",
        ))
        return {"report_id": report.id}

    monkeypatch.setattr(tasks, "_execute_daily_effects", report_effect)
    result = tasks.run_daily_tasks(
        datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc),
    )
    session.expire_all()
    run = session.query(SchedulerRun).filter_by(job_key=tasks.DAILY_JOB_KEY).one()
    assert result["executed"] is True
    assert run.status == "SUCCEEDED"
    assert session.query(ContinuousImprovementExecutiveAuditSnapshot).count() == 1
    assert session.query(AuditLog).filter_by(action="P4F1_DAILY_EFFECT").count() == 1
    assert _event_count(session) == 0


def test_daily_report_rolls_back_with_failed_run_checkpoint(db, monkeypatch):
    session, tmp_path = db
    _execution(session, tmp_path)
    monkeypatch.setattr(
        tasks, "SessionLocal",
        sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False),
    )

    def fail_after_report(task_db, financial_date):
        persist_report(task_db, None)
        task_db.add(AuditLog(
            action="P4F1_DAILY_EFFECT", entity_type="scheduler_test",
            entity_id=financial_date.isoformat(), details="must roll back",
        ))
        raise RuntimeError("injected_failure_after_report")

    monkeypatch.setattr(tasks, "_execute_daily_effects", fail_after_report)
    with pytest.raises(RuntimeError, match="injected_failure_after_report"):
        tasks.run_daily_tasks(datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc))
    session.expire_all()
    run = session.query(SchedulerRun).filter_by(job_key=tasks.DAILY_JOB_KEY).one()
    assert run.status == "FAILED"
    assert session.query(ContinuousImprovementExecutiveAuditSnapshot).count() == 0
    assert session.query(AuditLog).filter_by(action="P4F1_DAILY_EFFECT").count() == 0
    assert _event_count(session) == 0


def test_daily_real_effect_chain_reaches_read_only_audit_builders(db, monkeypatch):
    session, tmp_path = db
    _execution(session, tmp_path)
    monkeypatch.setattr(
        tasks, "SessionLocal",
        sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False),
    )
    monkeypatch.setattr(tasks, "queue_installment_reminders", lambda *a, **k: 0)
    monkeypatch.setattr(
        tasks, "accrue_overdue_penalties",
        lambda *a, **k: {"installments": 0, "penalty_total": 0},
    )
    for name in (
        "run_collection_cycle", "sync_cases", "sync_workflow_escalations",
        "sync_workflow_orchestration", "sync_execution_states", "verify_all",
        "sync_incidents", "sync_capa_recurrence", "sync_alerts",
        "sync_response_plans",
    ):
        monkeypatch.setattr(tasks, name, lambda *a, **k: {})
    snapshot = lambda *a, **k: (SimpleNamespace(id=1), {"status": "PASS", "risk_score": 0})
    for name in (
        "persist_risk_snapshot", "persist_compliance_snapshot",
        "persist_executive_dashboard", "persist_dashboard",
        "persist_improvement_dashboard",
    ):
        monkeypatch.setattr(tasks, name, snapshot)
    monkeypatch.setattr(
        tasks, "persist_improvement_priority",
        lambda *a, **k: (SimpleNamespace(id=1), {"counts": {}}),
    )
    monkeypatch.setattr(
        tasks, "persist_improvement_balancing",
        lambda *a, **k: (SimpleNamespace(id=1), {"status": "PASS", "unassigned": []}),
    )
    monkeypatch.setattr(tasks, "persist_finalization", lambda *a, **k: {})

    result = tasks.run_daily_tasks(
        datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc),
    )

    session.expire_all()
    run = session.query(SchedulerRun).filter_by(job_key=tasks.DAILY_JOB_KEY).one()
    assert result["executed"] is True and run.status == "SUCCEEDED"
    assert session.query(ContinuousImprovementAuditSnapshot).count() == 1
    assert session.query(ContinuousImprovementExecutiveAuditSnapshot).count() == 1
    assert _event_count(session) == 0


def test_snapshot_verification_uses_read_only_inspection(db):
    session, tmp_path = db
    execution, _ = _execution(session, tmp_path)
    cycle_row, _ = persist_cycle_audit(session, execution.id)
    report_row, _ = persist_report(session)
    session.commit()
    assert verify_snapshot(session, cycle_row.id)["hash_valid"] is True
    assert verify_report(session, report_row.id)["hash_valid"] is True
    assert _event_count(session) == 0
    assert not session.new and not session.dirty
