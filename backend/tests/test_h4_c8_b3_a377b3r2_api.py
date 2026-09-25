"""HTTP contract for the annual closing administration workflow."""

from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib

import pytest
import sqlalchemy as sa
from fastapi import Depends, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import admin_cycle_annual_closing as annual_api
from app.api.deps import current_user
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import (
    AuditLog, Contribution, Cycle, CycleAnnualClosing, CycleAnnualClosingCashEvidence,
    CycleAnnualClosingReview, CycleAnnualClosingSnapshot, CycleParticipation, Group,
    Member, OperationalWorkflowOrchestration, OperationalWorkflowTask, User,
    WorkflowExecutionEvidence, WorkflowExecutionEvidenceFile,
)
from app.services.ledger import post_entry
from app.services.workflow_evidence_storage_v068 import _storage_path


CUTOFF = datetime(2027, 12, 10, 18, tzinfo=timezone.utc)
CUTOFF_TEXT = CUTOFF.isoformat()
CYCLE_URL = "/api/admin/finance/cycles/1/annual-closing"


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workflow_evidence_storage_root", str(tmp_path / "workflow-evidence"))
    engine = sa.create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    def test_db():
        with factory() as db:
            yield db

    def test_user(request: Request, db: Session = Depends(get_db)):
        actor = request.headers.get("X-Test-Actor")
        if actor is None:
            raise HTTPException(401, "authentication required")
        user = db.get(User, int(actor))
        if user is None or not user.is_active:
            raise HTTPException(401, "unknown actor")
        return user

    previous_db = app.dependency_overrides.get(get_db)
    previous_user = app.dependency_overrides.get(current_user)
    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[current_user] = test_user
    try:
        with TestClient(app) as client:
            yield client, factory
    finally:
        if previous_db is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous_db
        if previous_user is None:
            app.dependency_overrides.pop(current_user, None)
        else:
            app.dependency_overrides[current_user] = previous_user
        engine.dispose()


def seed(factory, *, paid=True, cash=Decimal("100.00"), make_file=True):
    with factory() as db:
        db.add_all([
            User(id=1, name="Member", email="member-r2@example.test", cpf="00000000000001",
                 password_hash="unused", role="USER"),
            User(id=2, name="Master", email="master-r2@example.test", cpf="00000000000002",
                 password_hash="unused", role="ADMIN", is_master=True),
            User(id=3, name="Admin", email="admin-r2@example.test", cpf="00000000000003",
                 password_hash="unused", role="ADMIN"),
            Group(id=1, name="Group R2"),
        ])
        db.flush()
        db.add(Member(id=1, user_id=1, group_id=1))
        db.add(Cycle(id=1, start_date=date(2026, 12, 10), entry_deadline=date(2027, 1, 10),
                     closing_reference_date=date(2027, 12, 10), monthly_amount=Decimal("150.00"),
                     months=12, max_quotas=50, status="OPEN"))
        db.flush()
        db.add(CycleParticipation(id=1, cycle_id=1, member_id=1, status="ACTIVE"))
        if paid:
            db.add(Contribution(id=1, member_id=1, cycle_id=1, competence=date(2027, 1, 1),
                                amount=Decimal("100.00"), status="PAID",
                                paid_amount=Decimal("100.00"),
                                paid_at=datetime(2027, 12, 9, 18, tzinfo=timezone.utc)))
        db.flush()
        if cash:
            post_entry(db, "CAIXINHA", "CREDIT", cash, "TEST_CASH", "1")
            db.flush()
        task = OperationalWorkflowTask(action_code="CLOSING_EVIDENCE", status="OPEN",
                                       priority="MEDIUM", created_by=3)
        db.add(task)
        db.flush()
        db.add(OperationalWorkflowOrchestration(
            task_id=task.id, priority="MEDIUM", sla_status="ON_TRACK",
            execution_state="IN_EXECUTION", started_by=3,
        ))
        evidence = WorkflowExecutionEvidence(
            task_id=task.id, added_by=3, evidence_type="ATTACHMENT", title="Cash position",
            content="stored evidence", content_hash=hashlib.sha256(b"stored evidence").hexdigest(),
        )
        db.add(evidence)
        db.flush()
        file_id = None
        if make_file:
            payload = b"stored cash statement for annual closing"
            key = "r2-cash-evidence.txt"
            path = _storage_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            file_row = WorkflowExecutionEvidenceFile(
                evidence_id=evidence.id, version=1, original_name="cash.txt",
                storage_key=key, content_type="text/plain", size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(), uploaded_by=3,
            )
            db.add(file_row)
            db.flush()
            file_id = file_row.id
        db.commit()
        return file_id


def headers(actor):
    return {"X-Test-Actor": str(actor)}


def without_auth_override(client, method, url, **kwargs):
    """Keep the real HTTPBearer/current_user path for missing-token assertions."""
    override = app.dependency_overrides.pop(current_user)
    try:
        return client.request(method, url, **kwargs)
    finally:
        app.dependency_overrides[current_user] = override


def cash_body(file_id, *, balance="100.00", observed=CUTOFF_TEXT, cutoff=CUTOFF_TEXT):
    return {"file_id": file_id, "declared_cash_balance": balance,
            "observed_at": observed, "closing_cutoff_at": cutoff}


def cash_and_review(client, file_id, *, balance="100.00"):
    cash = client.post(f"{CYCLE_URL}/cash-evidence", json=cash_body(file_id, balance=balance),
                       headers=headers(2))
    assert cash.status_code == 200, cash.text
    closing_id = cash.json()["closing_id"]
    review = client.post(
        f"/api/admin/finance/annual-closings/{closing_id}/reviews",
        json={"expected_state_revision": 0, "closing_cutoff_at": CUTOFF_TEXT,
              "cash_evidence_id": cash.json()["id"]}, headers=headers(3),
    )
    assert review.status_code == 200, review.text
    return cash.json(), review.json()


def test_auth_and_read_only_preview(api):
    client, factory = api
    seed(factory)
    preview_url = f"{CYCLE_URL}/preview"
    params = {"closing_cutoff_at": CUTOFF_TEXT}
    missing_auth = without_auth_override(client, "GET", preview_url, params=params)
    assert missing_auth.status_code == 401
    assert missing_auth.headers["WWW-Authenticate"] == "Bearer"
    assert client.get(preview_url, params=params, headers=headers(1)).status_code == 403
    assert client.get(CYCLE_URL, headers=headers(1)).status_code == 403
    assert client.get(preview_url, params=params, headers=headers(3)).status_code == 200
    preview = client.get(preview_url, params=params, headers=headers(3)).json()
    assert preview["gross_realized_result"] == "0.00"
    assert preview["administration_fee"] == "0.00"
    assert "source_trace" not in preview and "participants" not in preview
    assert client.get(preview_url, params={"closing_cutoff_at": "2027-12-10T18:00:00"},
                      headers=headers(3)).status_code == 422
    assert client.get("/api/admin/finance/cycles/999/annual-closing/preview",
                      params=params, headers=headers(3)).status_code == 404
    assert client.get(CYCLE_URL, headers=headers(3)).status_code == 404
    with factory() as db:
        assert db.query(CycleAnnualClosing).count() == 0
        assert db.query(CycleAnnualClosingReview).count() == 0
        assert db.query(CycleAnnualClosingSnapshot).count() == 0
        assert db.query(CycleAnnualClosingCashEvidence).count() == 0
        assert db.query(AuditLog).count() == 0


@pytest.mark.parametrize("body_patch", [
    {"sha256": "a" * 64}, {"attested_by": 1},
    {"declared_cash_balance": "100.001"},
    {"declared_cash_balance": 100.0},
    {"observed_at": "2027-12-10T18:00:00"},
    {"closing_cutoff_at": "2027-12-10T18:00:00"},
    {"file_id": 0},
])
def test_cash_request_validation(api, body_patch):
    client, factory = api
    file_id = seed(factory)
    body = cash_body(file_id)
    body.update(body_patch)
    assert client.post(f"{CYCLE_URL}/cash-evidence", json=body,
                       headers=headers(2)).status_code == 422


def test_cash_auth_semantics_integrity_and_retry(api):
    client, factory = api
    file_id = seed(factory)
    url = f"{CYCLE_URL}/cash-evidence"
    body = cash_body(file_id)
    missing_auth = without_auth_override(client, "POST", url, json=body)
    assert missing_auth.status_code == 401
    assert missing_auth.headers["WWW-Authenticate"] == "Bearer"
    assert client.post(url, json=body, headers=headers(1)).status_code == 403
    assert client.post(url, json=body, headers=headers(3)).status_code == 403
    assert client.post(url, json=cash_body(999), headers=headers(2)).status_code == 404
    assert client.post(url, json=cash_body(file_id, observed="2027-12-10T18:00:01Z"),
                       headers=headers(2)).status_code == 400
    with factory() as db:
        assert db.query(CycleAnnualClosing).count() == 0
    first = client.post(url, json=body, headers=headers(2))
    second = client.post(url, json=body, headers=headers(2))
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["attested_by"] == 2
    assert first.json()["uploaded_by"] == 3
    assert "storage_key" not in first.json() and "sha256" not in first.json()
    with factory() as db:
        assert db.query(CycleAnnualClosingCashEvidence).count() == 1
        row = db.get(WorkflowExecutionEvidenceFile, file_id)
        row.revoked_at = datetime.now(timezone.utc)
        db.commit()
    assert client.post(url, json=body, headers=headers(2)).status_code == 409


def test_cash_corrupted_stored_bytes_are_conflict(api):
    client, factory = api
    file_id = seed(factory)
    _storage_path("r2-cash-evidence.txt").write_bytes(b"corrupted")
    assert client.post(f"{CYCLE_URL}/cash-evidence", json=cash_body(file_id),
                       headers=headers(2)).status_code == 409
    with factory() as db:
        assert db.query(CycleAnnualClosing).count() == 0
        assert db.query(CycleAnnualClosingCashEvidence).count() == 0


def test_prepare_state_approval_close_and_strict_retry(api):
    client, factory = api
    file_id = seed(factory)
    cash, review = cash_and_review(client, file_id)
    closing_id = cash["closing_id"]
    base = f"/api/admin/finance/annual-closings/{closing_id}"
    state = client.get(CYCLE_URL, headers=headers(3))
    assert state.status_code == 200
    assert state.json()["state_revision"] == 1
    assert state.json()["latest_review"]["review_id"] == review["review_id"]
    assert state.json()["latest_review"]["ledger_cash_balance"] == "100.00"
    assert state.json()["latest_review"]["reconciliation_difference"] == "0.00"
    for hidden in ("storage_key", "canonical_payload", "reconciliation_payload", "ledger_entries"):
        assert hidden not in state.text
    retry = client.post(f"{base}/reviews", json={
        "expected_state_revision": 1, "closing_cutoff_at": CUTOFF_TEXT,
        "cash_evidence_id": cash["id"],
    }, headers=headers(3))
    assert retry.status_code == 200 and retry.json()["review_id"] == review["review_id"]
    assert client.post(f"{base}/reviews", json={
        "expected_state_revision": 0, "closing_cutoff_at": CUTOFF_TEXT,
        "cash_evidence_id": cash["id"],
    }, headers=headers(3)).status_code == 409
    approve_url = f"{base}/reviews/{review['review_id']}/approve"
    assert client.post(approve_url, json={"expected_state_revision": 1},
                       headers=headers(3)).status_code == 403
    assert client.post(f"{base}/reviews/999/approve",
                       json={"expected_state_revision": 1}, headers=headers(2)).status_code == 404
    assert client.post(approve_url, json={"expected_state_revision": 0},
                       headers=headers(2)).status_code == 409
    approved = client.post(approve_url, json={"expected_state_revision": 1}, headers=headers(2))
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "MASTER_APPROVED"
    assert approved.json()["approved_by"] == 2
    close_url = f"{base}/close"
    assert client.post(close_url, json={"expected_state_revision": 2,
                                        "closing_cutoff_at": CUTOFF_TEXT},
                       headers=headers(3)).status_code == 403
    assert client.post(close_url, json={"expected_state_revision": 2,
                                        "closing_cutoff_at": "2027-12-10T18:00:01Z"},
                       headers=headers(2)).status_code == 409
    closed = client.post(close_url, json={"expected_state_revision": 2,
                                          "closing_cutoff_at": CUTOFF_TEXT}, headers=headers(2))
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "CLOSED"
    assert closed.json()["state_revision"] == 3
    assert closed.json()["created_by"] == 2
    same = client.post(close_url, json={"expected_state_revision": 3,
                                        "closing_cutoff_at": CUTOFF_TEXT}, headers=headers(2))
    assert same.status_code == 200 and same.json()["snapshot_id"] == closed.json()["snapshot_id"]
    assert client.post(close_url, json={"expected_state_revision": 2,
                                        "closing_cutoff_at": CUTOFF_TEXT},
                       headers=headers(2)).status_code == 409
    with factory() as db:
        assert db.query(CycleAnnualClosingSnapshot).count() == 1
        assert db.query(CycleAnnualClosingReview).count() == 1
        assert db.query(AuditLog).filter(AuditLog.action.like("CYCLE_ANNUAL_CLOSING_%")).count() == 3
        assert db.get(CycleAnnualClosing, closing_id).state_revision == 3


def test_prepare_financial_gates_and_rollback(api, monkeypatch):
    client, factory = api
    file_id = seed(factory)
    cash = client.post(f"{CYCLE_URL}/cash-evidence", json=cash_body(file_id), headers=headers(2)).json()
    closing_id = cash["closing_id"]
    url = f"/api/admin/finance/annual-closings/{closing_id}/reviews"
    body = {"expected_state_revision": 0, "closing_cutoff_at": CUTOFF_TEXT,
            "cash_evidence_id": cash["id"]}
    assert client.post(url, json={"closing_cutoff_at": CUTOFF_TEXT,
                                  "cash_evidence_id": cash["id"]}, headers=headers(3)).status_code == 422
    with monkeypatch.context() as patch:
        def fail_after_write(db, **kwargs):
            db.add(AuditLog(actor_user_id=3, action="TEST_ROLLBACK", entity_type="TEST",
                            entity_id="1", details="temporary"))
            db.flush()
            raise ValueError("annual closing cash reconciliation differs from zero")
        patch.setattr(annual_api, "prepare_cycle_closing_review", fail_after_write)
        assert client.post(url, json=body, headers=headers(3)).status_code == 409
    with factory() as db:
        assert db.query(AuditLog).filter(AuditLog.action == "TEST_ROLLBACK").count() == 0
        assert db.query(CycleAnnualClosingReview).count() == 0
        assert db.get(CycleAnnualClosing, closing_id).status == "ASSESSING"
        assert db.get(CycleAnnualClosing, closing_id).state_revision == 0


@pytest.mark.parametrize("message", [
    "annual closing preview has blocking source gaps",
    "annual closing cash reconciliation differs from zero",
    "annual closing has insufficient reconciled liquidity",
    "cash evidence file link is stale or revoked",
    "ledger integrity cannot prove annual cash position",
])
def test_prepare_service_financial_conflicts(api, monkeypatch, message):
    client, factory = api
    file_id = seed(factory)
    cash = client.post(f"{CYCLE_URL}/cash-evidence", json=cash_body(file_id), headers=headers(2)).json()
    def blocked(db, **kwargs):
        raise ValueError(message)
    monkeypatch.setattr(annual_api, "prepare_cycle_closing_review", blocked)
    response = client.post(
        f"/api/admin/finance/annual-closings/{cash['closing_id']}/reviews",
        json={"expected_state_revision": 0, "closing_cutoff_at": CUTOFF_TEXT,
              "cash_evidence_id": cash["id"]}, headers=headers(3),
    )
    assert response.status_code == 409


@pytest.mark.parametrize("balance", ["99.99", "100.01"])
def test_real_reconciliation_cent_difference_blocks_prepare(api, balance):
    client, factory = api
    file_id = seed(factory)
    cash = client.post(f"{CYCLE_URL}/cash-evidence",
                       json=cash_body(file_id, balance=balance), headers=headers(2)).json()
    response = client.post(
        f"/api/admin/finance/annual-closings/{cash['closing_id']}/reviews",
        json={"expected_state_revision": 0, "closing_cutoff_at": CUTOFF_TEXT,
              "cash_evidence_id": cash["id"]}, headers=headers(3),
    )
    assert response.status_code == 409
    with factory() as db:
        closing = db.get(CycleAnnualClosing, cash["closing_id"])
        assert closing.status == "ASSESSING" and closing.state_revision == 0
        assert db.query(CycleAnnualClosingReview).count() == 0


def test_real_insufficient_liquidity_blocks_prepare(api):
    client, factory = api
    file_id = seed(factory, cash=Decimal("99.00"))
    cash = client.post(f"{CYCLE_URL}/cash-evidence",
                       json=cash_body(file_id, balance="99.00"), headers=headers(2)).json()
    response = client.post(
        f"/api/admin/finance/annual-closings/{cash['closing_id']}/reviews",
        json={"expected_state_revision": 0, "closing_cutoff_at": CUTOFF_TEXT,
              "cash_evidence_id": cash["id"]}, headers=headers(3),
    )
    assert response.status_code == 409
    with factory() as db:
        assert db.query(CycleAnnualClosingReview).count() == 0


def test_old_review_is_not_approvable(api):
    client, factory = api
    file_id = seed(factory)
    cash, first_review = cash_and_review(client, file_id)
    with factory() as db:
        first_file = db.get(WorkflowExecutionEvidenceFile, file_id)
        payload = b"second real cash statement"
        path = _storage_path("r2-cash-evidence-second.txt")
        path.write_bytes(payload)
        second_file = WorkflowExecutionEvidenceFile(
            evidence_id=first_file.evidence_id, version=2, original_name="cash-second.txt",
            storage_key="r2-cash-evidence-second.txt", content_type="text/plain",
            size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest(), uploaded_by=3,
        )
        db.add(second_file)
        db.commit()
        second_file_id = second_file.id
    second_cash = client.post(f"{CYCLE_URL}/cash-evidence",
                              json=cash_body(second_file_id), headers=headers(2))
    assert second_cash.status_code == 200
    base = f"/api/admin/finance/annual-closings/{cash['closing_id']}"
    second_review = client.post(f"{base}/reviews", json={
        "expected_state_revision": 1, "closing_cutoff_at": CUTOFF_TEXT,
        "cash_evidence_id": second_cash.json()["id"],
    }, headers=headers(3))
    assert second_review.status_code == 200
    assert second_review.json()["review_id"] != first_review["review_id"]
    assert client.post(f"{base}/reviews/{first_review['review_id']}/approve",
                       json={"expected_state_revision": 2}, headers=headers(2)).status_code == 409
    assert client.post(f"{base}/reviews/{second_review.json()['review_id']}/approve",
                       json={"expected_state_revision": 2}, headers=headers(2)).status_code == 200


def test_changed_ledger_blocks_close_without_snapshot(api):
    client, factory = api
    file_id = seed(factory)
    cash, review = cash_and_review(client, file_id)
    base = f"/api/admin/finance/annual-closings/{cash['closing_id']}"
    assert client.post(f"{base}/reviews/{review['review_id']}/approve",
                       json={"expected_state_revision": 1}, headers=headers(2)).status_code == 200
    with factory() as db:
        post_entry(db, "CAIXINHA", "CREDIT", Decimal("1.00"), "TEST_CASH", "later")
        db.commit()
    response = client.post(f"{base}/close", json={"expected_state_revision": 2,
                                                  "closing_cutoff_at": CUTOFF_TEXT},
                           headers=headers(2))
    assert response.status_code == 409
    with factory() as db:
        closing = db.get(CycleAnnualClosing, cash["closing_id"])
        assert closing.status == "MASTER_APPROVED" and closing.state_revision == 2
        assert db.query(CycleAnnualClosingSnapshot).count() == 0


def test_revoked_file_after_prepare_blocks_approval(api):
    client, factory = api
    file_id = seed(factory)
    cash, review = cash_and_review(client, file_id)
    with factory() as db:
        db.get(WorkflowExecutionEvidenceFile, file_id).revoked_at = datetime.now(timezone.utc)
        db.commit()
    response = client.post(
        f"/api/admin/finance/annual-closings/{cash['closing_id']}/reviews/{review['review_id']}/approve",
        json={"expected_state_revision": 1}, headers=headers(2),
    )
    assert response.status_code == 409
    with factory() as db:
        closing = db.get(CycleAnnualClosing, cash["closing_id"])
        assert closing.status == "READY_FOR_REVIEW" and closing.state_revision == 1


def test_close_revoked_file_preserves_approved_state(api):
    client, factory = api
    file_id = seed(factory)
    cash, review = cash_and_review(client, file_id)
    base = f"/api/admin/finance/annual-closings/{cash['closing_id']}"
    assert client.post(f"{base}/reviews/{review['review_id']}/approve",
                       json={"expected_state_revision": 1}, headers=headers(2)).status_code == 200
    with factory() as db:
        db.get(WorkflowExecutionEvidenceFile, file_id).revoked_at = datetime.now(timezone.utc)
        db.commit()
    response = client.post(f"{base}/close", json={"expected_state_revision": 2,
                                                  "closing_cutoff_at": CUTOFF_TEXT},
                           headers=headers(2))
    assert response.status_code == 409
    with factory() as db:
        closing = db.get(CycleAnnualClosing, cash["closing_id"])
        assert closing.status == "MASTER_APPROVED" and closing.state_revision == 2
        assert db.query(CycleAnnualClosingSnapshot).count() == 0
