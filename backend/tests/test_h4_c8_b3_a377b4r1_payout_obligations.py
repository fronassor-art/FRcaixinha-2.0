"""A3.77B4-R1 proposed: immutable payout obligations from a closing snapshot."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from threading import Barrier

import pytest
import sqlalchemy as sa
from fastapi import UploadFile
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    AuditLog,
    Contribution,
    Cycle,
    CycleAnnualClosing,
    CycleAnnualClosingPayoutObligation,
    CycleAnnualClosingReview,
    CycleAnnualClosingSnapshot,
    CycleParticipation,
    Group,
    Member,
    OperationalWorkflowOrchestration,
    OperationalWorkflowTask,
    User,
    WorkflowExecutionEvidence,
)
from app.services.cycle_closing_persistence import (
    create_or_get_cycle_annual_closing,
    persist_approved_cycle_closing_snapshot,
    snapshot_payload_hash,
)
from app.services.cycle_closing_payout_obligations import (
    PayoutObligationConflict,
    materialize_cycle_annual_closing_payout_obligations,
)
from app.services.cycle_closing_workflow import (
    approve_cycle_closing_review,
    prepare_cycle_closing_review,
    record_cycle_annual_closing_cash_evidence,
)
from app.services.ledger import post_entry
from app.services.workflow_evidence_storage_v068 import upload_file


D = Decimal
CUTOFF = datetime(2027, 12, 10, 18, tzinfo=timezone.utc)
CENT = D("0.01")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "workflow_evidence_storage_root", str(tmp_path / "evidence"))
    engine = sa.create_engine(
        f"sqlite:///{tmp_path / 'payout-obligations.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    migration_path = Path(__file__).parents[1] / "alembic/versions/0100_cycle_payout_obligations_a377b4r1.py"
    spec = spec_from_file_location("a377b4r1_migration", migration_path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        migration._create_sqlite_insert_guard(connection)
        migration._create_sqlite_immutability_guards(connection)

    with Session(engine, expire_on_commit=False) as session:
        session.info["engine"] = engine
        yield session
        session.rollback()
    engine.dispose()


def _seed_and_close(db, *, close=True):
    db.add_all([
        User(id=1, name="Master", email="master@payout.test", cpf="00000000000001",
             password_hash="unused", role="ADMIN", is_master=True),
        User(id=2, name="Member One", email="member1@payout.test", cpf="00000000000002",
             password_hash="unused"),
        User(id=3, name="Member Two", email="member2@payout.test", cpf="00000000000003",
             password_hash="unused"),
        User(id=4, name="Admin", email="admin@payout.test", cpf="00000000000004",
             password_hash="unused", role="ADMIN"),
        Group(id=1, name="Payout Test Group"),
    ])
    db.flush()
    db.add_all([
        Member(id=1, user_id=2, group_id=1),
        Member(id=2, user_id=3, group_id=1),
        Cycle(id=1, start_date=date(2026, 12, 10), entry_deadline=date(2027, 1, 10),
              closing_reference_date=date(2027, 12, 10), monthly_amount=D("150.00"),
              months=12, max_quotas=50, status="OPEN"),
    ])
    db.flush()
    db.add_all([
        CycleParticipation(id=1, cycle_id=1, member_id=1, status="ACTIVE"),
        CycleParticipation(id=2, cycle_id=1, member_id=2, status="ACTIVE"),
        Contribution(id=1, member_id=1, cycle_id=1, competence=date(2027, 1, 1),
                     amount=D("100.00"), status="PAID", paid_amount=D("100.00"),
                     paid_at=datetime(2027, 12, 9, 18, tzinfo=timezone.utc)),
    ])
    db.flush()
    post_entry(db, "CAIXINHA", "CREDIT", D("100.00"), "TEST_CASH", "payout-seed")
    db.flush()
    task = OperationalWorkflowTask(
        action_code="CLOSING_EVIDENCE", status="OPEN", priority="MEDIUM", created_by=4,
    )
    db.add(task)
    db.flush()
    parent = WorkflowExecutionEvidence(
        task_id=task.id, added_by=4, evidence_type="ATTACHMENT", title="Cash statement",
        content="stored", content_hash=sha256(b"stored evidence").hexdigest(),
    )
    db.add(parent)
    db.flush()
    orchestration = OperationalWorkflowOrchestration(
        task_id=task.id, priority="MEDIUM", sla_status="ON_TRACK",
        execution_state="IN_EXECUTION", started_by=4,
    )
    db.add(orchestration)
    db.flush()
    file_row = upload_file(
        db, task, parent, 4,
        UploadFile(filename="cash.txt", file=__import__("io").BytesIO(b"stored evidence"),
                   headers={"content-type": "text/plain"}),
    )
    closing = create_or_get_cycle_annual_closing(db, cycle_id=1, created_by=4)
    if not close:
        return closing, None, None
    cash = record_cycle_annual_closing_cash_evidence(
        db, closing_id=closing.id, file_id=file_row.id,
        declared_cash_balance=D("100.00"), observed_at=CUTOFF,
        closing_cutoff_at=CUTOFF, attested_by=1,
    )
    review = prepare_cycle_closing_review(
        db, closing_id=closing.id, expected_state_revision=closing.state_revision,
        closing_cutoff_at=CUTOFF, cash_evidence_id=cash.id, actor_id=4,
    )
    approve_cycle_closing_review(
        db, closing_id=closing.id, review_id=review.id,
        expected_state_revision=closing.state_revision, actor_id=1,
    )
    snapshot = persist_approved_cycle_closing_snapshot(
        db, closing_id=closing.id, closing_cutoff_at=CUTOFF,
        expected_state_revision=closing.state_revision, closed_by=1,
    )
    db.flush()
    return closing, review, snapshot


def _obligations(db, snapshot_id):
    return db.scalars(
        select(CycleAnnualClosingPayoutObligation)
        .where(CycleAnnualClosingPayoutObligation.snapshot_id == snapshot_id)
        .order_by(CycleAnnualClosingPayoutObligation.id)
    ).all()


def _rewrite_snapshot_payload(db, snapshot, change):
    payload = json.loads(snapshot.canonical_payload)
    change(payload)
    memory = deepcopy(payload)
    memory.pop("calculation_hash")
    encoded_memory = json.dumps(memory, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload["calculation_hash"] = sha256(encoded_memory.encode("utf-8")).hexdigest()
    from app.services.cycle_closing_persistence import canonical_snapshot_payload

    canonical = canonical_snapshot_payload(payload)
    digest = snapshot_payload_hash(canonical)
    db.execute(
        text("UPDATE cycle_annual_closing_snapshots SET canonical_payload=:payload, payload_hash=:hash WHERE id=:id"),
        {"payload": canonical, "hash": digest, "id": snapshot.id},
    )
    db.expire(snapshot)
    return canonical, digest


def _manual_obligation(db, snapshot, closing, *, member_id=1, participation_id=1,
                       amount=D("100.00"), source_hash=None):
    row = CycleAnnualClosingPayoutObligation(
        snapshot_id=snapshot.id, closing_id=closing.id, cycle_id=closing.cycle_id,
        member_id=member_id, cycle_participation_id=participation_id, amount=amount,
        source_payload_hash=source_hash or snapshot.payload_hash,
    )
    db.add(row)
    db.flush()
    return row


def _insert_obligation_direct(
    db, *, snapshot_id, closing_id, cycle_id, member_id,
    participation_id, source_hash, amount="100.00",
):
    return db.execute(text("""
        INSERT INTO cycle_annual_closing_payout_obligations (
            snapshot_id, closing_id, cycle_id, member_id,
            cycle_participation_id, amount, source_payload_hash, created_at
        ) VALUES (
            :snapshot_id, :closing_id, :cycle_id, :member_id,
            :participation_id, :amount, :source_hash, CURRENT_TIMESTAMP
        )
    """), {
        "snapshot_id": snapshot_id,
        "closing_id": closing_id,
        "cycle_id": cycle_id,
        "member_id": member_id,
        "participation_id": participation_id,
        "amount": amount,
        "source_hash": source_hash,
    })


def test_database_insert_guard_rejects_snapshot_hash_mismatch(db):
    closing, _review, snapshot = _seed_and_close(db)

    with pytest.raises(sa.exc.IntegrityError, match="snapshot linkage is invalid"):
        with db.begin_nested():
            _insert_obligation_direct(
                db, snapshot_id=snapshot.id, closing_id=closing.id,
                cycle_id=closing.cycle_id, member_id=1, participation_id=1,
                source_hash="b" * 64,
            )

    assert _obligations(db, snapshot.id) == []


def test_database_insert_guard_rejects_participation_for_another_member(db):
    closing, _review, snapshot = _seed_and_close(db)

    with pytest.raises(sa.exc.IntegrityError, match="participation linkage is invalid"):
        with db.begin_nested():
            _insert_obligation_direct(
                db, snapshot_id=snapshot.id, closing_id=closing.id,
                cycle_id=closing.cycle_id, member_id=1, participation_id=2,
                source_hash=snapshot.payload_hash,
            )

    assert _obligations(db, snapshot.id) == []


def test_database_insert_guard_rejects_snapshot_from_another_closing_cycle(db):
    closing, _review, snapshot = _seed_and_close(db)
    other_cycle = Cycle(
        id=2, start_date=date(2028, 12, 10), entry_deadline=date(2029, 1, 10),
        closing_reference_date=date(2029, 12, 10), monthly_amount=D("150.00"),
        months=12, max_quotas=50, status="OPEN",
    )
    db.add(other_cycle)
    db.flush()
    other_participation = CycleParticipation(
        id=20, cycle_id=other_cycle.id, member_id=1, status="ACTIVE",
    )
    other_closing = CycleAnnualClosing(
        cycle_id=other_cycle.id, status="CLOSED", created_by=4,
    )
    db.add_all([other_participation, other_closing])
    db.flush()

    with pytest.raises(sa.exc.IntegrityError, match="snapshot linkage is invalid"):
        with db.begin_nested():
            _insert_obligation_direct(
                db, snapshot_id=snapshot.id, closing_id=other_closing.id,
                cycle_id=other_cycle.id, member_id=1,
                participation_id=other_participation.id,
                source_hash=snapshot.payload_hash,
            )

    assert _obligations(db, snapshot.id) == []
    assert closing.status == "CLOSED"


def test_database_insert_guard_requires_closing_still_be_closed(db):
    closing, _review, snapshot = _seed_and_close(db)

    with pytest.raises(sa.exc.IntegrityError, match="snapshot linkage is invalid"):
        with db.begin_nested():
            db.execute(
                text("UPDATE cycle_annual_closings SET status='MASTER_APPROVED' WHERE id=:id"),
                {"id": closing.id},
            )
            _insert_obligation_direct(
                db, snapshot_id=snapshot.id, closing_id=closing.id,
                cycle_id=closing.cycle_id, member_id=1, participation_id=1,
                source_hash=snapshot.payload_hash,
            )

    db.refresh(closing)
    assert closing.status == "CLOSED"
    assert _obligations(db, snapshot.id) == []


def test_materializes_one_exact_obligation_per_snapshot_participant_including_zero(db):
    closing, review, snapshot = _seed_and_close(db)
    original_payload = snapshot.canonical_payload
    original_hash = snapshot.payload_hash
    original_status, original_revision = closing.status, closing.state_revision

    rows = materialize_cycle_annual_closing_payout_obligations(db, closing_id=closing.id)

    assert len(rows) == 2
    by_member = {row.member_id: row for row in rows}
    assert by_member[1].cycle_participation_id == 1
    assert by_member[1].amount == D("100.00")
    assert by_member[2].cycle_participation_id == 2
    assert by_member[2].amount == D("0.00")
    assert all(row.source_payload_hash == original_hash for row in rows)
    assert sum((row.amount for row in rows), D("0.00")) == review.participant_payout_liability
    assert review.participant_payout_liability == D("100.00")
    assert snapshot.canonical_payload == original_payload
    assert snapshot.payload_hash == original_hash
    assert closing.status == original_status == "CLOSED"
    assert closing.state_revision == original_revision
    assert closing.approved_at is not None and closing.closed_at is not None


def test_retry_is_idempotent_and_returns_same_rows_without_duplicate_obligations(db):
    closing, _review, snapshot = _seed_and_close(db)
    first = materialize_cycle_annual_closing_payout_obligations(db, closing_id=closing.id)
    first_ids = [row.id for row in first]
    db.flush()

    second = materialize_cycle_annual_closing_payout_obligations(db, closing_id=closing.id)

    assert [row.id for row in second] == first_ids
    assert len(_obligations(db, snapshot.id)) == 2


def test_partial_existing_set_is_an_explicit_conflict_without_completion(db):
    closing, _review, snapshot = _seed_and_close(db)
    _manual_obligation(db, snapshot, closing)
    db.flush()

    with pytest.raises(PayoutObligationConflict, match="partial or differs"):
        materialize_cycle_annual_closing_payout_obligations(db, closing_id=closing.id)

    assert len(_obligations(db, snapshot.id)) == 1


@pytest.mark.parametrize(
    "member_id, participation_id",
    [(1, 2), (2, 1)],
    ids=["unique-snapshot-member", "unique-snapshot-participation"],
)
def test_database_prevents_duplicate_member_or_participation_per_snapshot(
    db, member_id, participation_id,
):
    closing, _review, snapshot = _seed_and_close(db)
    materialize_cycle_annual_closing_payout_obligations(db, closing_id=closing.id)
    with pytest.raises(sa.exc.IntegrityError):
        with db.begin_nested():
            _manual_obligation(
                db, snapshot, closing, member_id=member_id,
                participation_id=participation_id, amount=D("0.00"),
            )
    assert len(_obligations(db, snapshot.id)) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": D("99.99")},
        {"source_hash": "b" * 64},
    ],
    ids=["divergent-amount", "divergent-source-hash"],
)
def test_complete_but_divergent_existing_set_is_not_repaired(db, changes):
    closing, _review, snapshot = _seed_and_close(db)
    _manual_obligation(db, snapshot, closing, **changes)
    _manual_obligation(db, snapshot, closing, member_id=2, participation_id=2,
                       amount=D("0.00"), source_hash=snapshot.payload_hash)

    with pytest.raises(PayoutObligationConflict, match="partial or differs"):
        materialize_cycle_annual_closing_payout_obligations(db, closing_id=closing.id)

    rows = _obligations(db, snapshot.id)
    assert len(rows) == 2
    assert rows[0].amount == changes.get("amount", D("100.00"))


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda rows: rows[0].update(member_id=999), "Member 999 does not exist"),
        (lambda rows: rows[0].update(cycle_participation_id=999), "CycleParticipation 999 does not exist"),
        (lambda rows: rows[0].update(cycle_participation_id=2), "different Member"),
    ],
    ids=["missing-member", "missing-participation", "participation-other-member"],
)
def test_invalid_snapshot_references_abort_before_any_rows_are_written(db, mutate, message):
    closing, _review, snapshot = _seed_and_close(db)
    _rewrite_snapshot_payload(db, snapshot, lambda payload: mutate(payload["participants"]))

    with pytest.raises(PayoutObligationConflict, match=message):
        materialize_cycle_annual_closing_payout_obligations(db, closing_id=closing.id)

    assert _obligations(db, snapshot.id) == []


def test_participation_from_another_cycle_is_rejected_atomically(db):
    closing, _review, snapshot = _seed_and_close(db)
    db.add(Cycle(id=2, start_date=date(2028, 12, 10), entry_deadline=date(2029, 1, 10),
                 closing_reference_date=date(2029, 12, 10), monthly_amount=D("150.00"),
                 months=12, max_quotas=50, status="OPEN"))
    db.flush()
    db.add(CycleParticipation(id=20, cycle_id=2, member_id=1, status="ACTIVE"))
    db.flush()
    _rewrite_snapshot_payload(
        db, snapshot, lambda payload: payload["participants"][0].update(cycle_participation_id=20),
    )

    with pytest.raises(PayoutObligationConflict, match="different Cycle"):
        materialize_cycle_annual_closing_payout_obligations(db, closing_id=closing.id)

    assert _obligations(db, snapshot.id) == []


def test_negative_snapshot_net_amount_is_rejected_without_rounding(db):
    closing, _review, snapshot = _seed_and_close(db)
    _rewrite_snapshot_payload(
        db, snapshot, lambda payload: payload["participants"][0].update(projected_net="-1.00"),
    )

    with pytest.raises(PayoutObligationConflict, match="non-negative"):
        materialize_cycle_annual_closing_payout_obligations(db, closing_id=closing.id)

    assert _obligations(db, snapshot.id) == []


def test_corrupted_snapshot_is_rejected(db):
    closing, _review, snapshot = _seed_and_close(db)
    db.execute(
        text("UPDATE cycle_annual_closing_snapshots SET payload_hash=:hash WHERE id=:id"),
        {"hash": "f" * 64, "id": snapshot.id},
    )
    db.expire(snapshot)

    with pytest.raises(PayoutObligationConflict, match="integrity verification"):
        materialize_cycle_annual_closing_payout_obligations(db, closing_id=closing.id)

    assert _obligations(db, snapshot.id) == []


def test_snapshot_total_must_equal_approved_review_liability_exactly(db):
    closing, review, snapshot = _seed_and_close(db)
    db.execute(
        text("""
            UPDATE cycle_annual_closing_reviews
            SET participant_payout_liability=:amount, required_liquidity=:amount
            WHERE id=:id
        """),
        {"amount": "100.01", "id": review.id},
    )
    db.expire_all()

    with pytest.raises(PayoutObligationConflict, match="does not equal the approved"):
        materialize_cycle_annual_closing_payout_obligations(db, closing_id=closing.id)

    assert _obligations(db, snapshot.id) == []


def test_unclosed_process_cannot_materialize_obligations(db):
    closing, _review, _snapshot = _seed_and_close(db, close=False)

    with pytest.raises(PayoutObligationConflict, match="must be CLOSED"):
        materialize_cycle_annual_closing_payout_obligations(db, closing_id=closing.id)

    assert closing.status == "ASSESSING"
    assert closing.state_revision == 0


def test_orm_update_and_delete_are_blocked(db):
    closing, _review, snapshot = _seed_and_close(db)
    row = materialize_cycle_annual_closing_payout_obligations(db, closing_id=closing.id)[0]
    row.amount = D("99.00")
    with pytest.raises(RuntimeError, match="imutável"):
        db.flush()
    db.rollback()

    closing, _review, snapshot = _seed_and_close(db)
    row = materialize_cycle_annual_closing_payout_obligations(db, closing_id=closing.id)[0]
    db.delete(row)
    with pytest.raises(RuntimeError, match="não pode ser excluído"):
        db.flush()
    db.rollback()


@pytest.mark.parametrize("statement", [
    "UPDATE cycle_annual_closing_payout_obligations SET amount='99.00' WHERE id=:id",
    "DELETE FROM cycle_annual_closing_payout_obligations WHERE id=:id",
])
def test_database_triggers_block_direct_update_and_delete(db, statement):
    closing, _review, snapshot = _seed_and_close(db)
    row = materialize_cycle_annual_closing_payout_obligations(db, closing_id=closing.id)[0]
    db.flush()

    with pytest.raises(sa.exc.DatabaseError):
        db.execute(text(statement), {"id": row.id})

    db.rollback()


def test_concurrent_materialization_serializes_and_does_not_duplicate(db):
    closing, _review, snapshot = _seed_and_close(db)
    db.commit()
    barrier = Barrier(2)
    engine = db.info["engine"]

    def materialize_in_session():
        with Session(engine, expire_on_commit=False) as session:
            barrier.wait(timeout=5)
            rows = materialize_cycle_annual_closing_payout_obligations(session, closing_id=closing.id)
            row_ids = tuple(row.id for row in rows)
            session.commit()
            return row_ids

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(materialize_in_session)
        second = pool.submit(materialize_in_session)
        first_ids = first.result(timeout=20)
        second_ids = second.result(timeout=20)

    assert first_ids == second_ids
    assert len(_obligations(db, snapshot.id)) == 2
    reloaded = db.get(CycleAnnualClosing, closing.id)
    assert reloaded.status == "CLOSED"
    assert reloaded.state_revision == closing.state_revision
