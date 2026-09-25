"""Read-only official annual closing statement service tests (A3.77B4-R5-R1)."""

from copy import deepcopy
from datetime import date, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    User,
    Member,
    Cycle,
    CycleParticipation,
)
from app.services.cycle_closing_payout_obligations import (
    materialize_cycle_annual_closing_payout_obligations,
)
from app.services.cycle_closing_persistence import snapshot_payload_hash
from app.services.cycle_closing_statements import (
    ClosingStatementError,
    get_cycle_annual_closing_calculation_memory,
    get_cycle_annual_closing_participant_statement,
    get_cycle_annual_closing_statement,
)
from app.services.cycle_closing_workflow import _canonical, _digest, _review_memory


D = Decimal
ZERO = D("0.00")
TABLES = (
    "cycle_annual_closings",
    "cycle_annual_closing_reviews",
    "cycle_annual_closing_snapshots",
    "cycle_annual_closing_payout_obligations",
    "audit_logs",
    "workflow_evidence_integrity_events",
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "workflow_evidence_storage_root", str(tmp_path / "evidence"))
    engine = sa.create_engine(
        f"sqlite:///{tmp_path / 'closing-statements.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    versions = Path(__file__).parents[1] / "alembic/versions"
    for module_name, filename, creators in (
        (
            "a377b5r1_obligations_migration",
            "0100_cycle_payout_obligations_a377b4r1.py",
            ("_create_sqlite_insert_guard", "_create_sqlite_immutability_guards"),
        ),
    ):
        spec = importlib.util.spec_from_file_location(module_name, versions / filename)
        migration = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(migration)
        with engine.begin() as connection:
            for creator in creators:
                getattr(migration, creator)(connection)

    with Session(engine, expire_on_commit=False) as session:
        session.info["engine"] = engine
        yield session
        session.rollback()
    engine.dispose()


def _obligation_helpers():
    path = Path(__file__).with_name("test_h4_c8_b3_a377b4r1_payout_obligations.py")
    spec = importlib.util.spec_from_file_location("a377b4r1_statement_helpers", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _seed(db):
    return _obligation_helpers()._seed_and_close(db)


def _counts(db):
    return {
        table: db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        for table in TABLES
    }


def _watch_dml(engine):
    statements = []

    def listener(_conn, _cursor, statement, _params, _context, _many):
        token = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
        if token in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", listener)
    return statements, listener


def _rewrite_snapshot(snapshot, edit):
    payload = json.loads(snapshot.canonical_payload)
    edit(payload)
    memory = deepcopy(payload)
    memory.pop("calculation_hash")
    encoded_memory = json.dumps(memory, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload["calculation_hash"] = sha256(encoded_memory.encode("utf-8")).hexdigest()
    # Deliberately preserve malformed/inconsistent memory in tamper cases; the
    # official verifier/service must reject it rather than repair it.
    snapshot.canonical_payload = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    snapshot.payload_hash = snapshot_payload_hash(snapshot.canonical_payload)


def _set_review_liability(review, liability):
    review.participant_payout_liability = liability
    review.required_liquidity = liability + review.administration_fee
    review.liquidity_surplus = review.actual_cash_balance - review.required_liquidity
    _resign_review(review)


def _resign_review(review):
    reconciliation = json.loads(review.reconciliation_payload)
    cutoff = review.closing_cutoff_at
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    reconciliation.update({
        "cycle_id": review.cycle_id,
        "cutoff": cutoff.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "calculation_hash": review.calculation_hash,
        "ledger_cash_balance": str(review.ledger_cash_balance),
        "actual_cash_balance": str(review.actual_cash_balance),
        "reconciliation_difference": str(review.reconciliation_difference),
        "participant_payout_liability": str(review.participant_payout_liability),
        "administration_fee": str(review.administration_fee),
        "required_liquidity": str(review.required_liquidity),
        "liquidity_surplus": str(review.liquidity_surplus),
    })
    review.reconciliation_payload = _canonical(reconciliation)
    review.reconciliation_hash = _digest(review.reconciliation_payload)
    review.review_hash = _digest(_canonical(_review_memory(review)))


def test_global_individual_and_memory_are_exact_and_deterministically_ordered(db):
    closing, review, snapshot = _seed(db)
    global_statement = get_cycle_annual_closing_statement(db, closing_id=closing.id)
    individual = get_cycle_annual_closing_participant_statement(
        db, closing_id=closing.id, member_id=1,
    )
    memory = get_cycle_annual_closing_calculation_memory(db, closing_id=closing.id)

    assert global_statement.snapshot_id == snapshot.id
    assert global_statement.payload_hash == snapshot.payload_hash
    assert global_statement.calculation_hash == review.calculation_hash
    assert global_statement.participant_count == 2
    assert global_statement.gross_realized_result == ZERO
    assert global_statement.distributable_result == ZERO
    assert global_statement.total_gross_share == global_statement.distributable_result
    assert global_statement.total_projected_net == review.participant_payout_liability == D("100.00")
    assert global_statement.total_projected_residual_debt == ZERO
    assert [row.cycle_participation_id for row in global_statement.participants] == [1, 2]
    assert individual.participant.member_id == 1
    assert individual.participant.projected_net == D("100.00")
    assert memory.source_trace.contributions[0].contribution_id == 1
    assert global_statement.payout_obligations_verified is None


@pytest.mark.parametrize("status", ["BLOCKED_DELINQUENCY", "VOLUNTARILY_EXITED"])
def test_frozen_participation_status_is_reported_without_live_member_status(db, status):
    closing, review, snapshot = _seed(db)
    _rewrite_snapshot(
        snapshot,
        lambda payload: payload["participants"][0].update(participation_status=status),
    )
    payload = json.loads(snapshot.canonical_payload)
    review.calculation_hash = payload["calculation_hash"]
    _resign_review(review)
    statement = get_cycle_annual_closing_participant_statement(
        db, closing_id=closing.id, member_id=1,
    )
    assert statement.participant.participation_status == status


@pytest.mark.parametrize(
    "owed, compensation, net, residual",
    [(D("10.00"), D("10.00"), D("90.00"), ZERO),
     (D("110.00"), D("100.00"), ZERO, D("10.00"))],
    ids=["compensation", "residual-debt"],
)
def test_compensation_and_residual_debt_are_read_from_snapshot(
    db, owed, compensation, net, residual,
):
    closing, review, snapshot = _seed(db)

    def edit(payload):
        participant = payload["participants"][0]
        participant["compensable_obligations"] = [{
            "source_id": "statement-test-obligation",
            "kind": "CONTRIBUTION_CHARGE",
            "amount": str(owed),
            "due_date": "2027-12-10",
            "loan_id": None,
            "agreement_id": None,
        }]
        participant["compensable_obligations_total"] = str(owed)
        participant["projected_compensation"] = str(compensation)
        participant["projected_net"] = str(net)
        participant["projected_residual_debt"] = str(residual)

    _rewrite_snapshot(snapshot, edit)
    review.calculation_hash = json.loads(snapshot.canonical_payload)["calculation_hash"]
    _set_review_liability(review, net)
    participant_statement = get_cycle_annual_closing_participant_statement(
        db, closing_id=closing.id, member_id=1,
    ).participant
    assert participant_statement.compensable_obligations_total == owed
    assert participant_statement.projected_compensation == compensation
    assert participant_statement.projected_net == net
    assert participant_statement.projected_residual_debt == residual


def test_services_make_no_dml_or_persisted_state_changes(db):
    closing, _review, _snapshot = _seed(db)
    before_counts = _counts(db)
    before_state = (closing.status, closing.state_revision)
    statements, listener = _watch_dml(db.info["engine"])
    try:
        get_cycle_annual_closing_statement(db, closing_id=closing.id)
        get_cycle_annual_closing_participant_statement(db, closing_id=closing.id, member_id=2)
        get_cycle_annual_closing_calculation_memory(db, closing_id=closing.id)
    finally:
        event.remove(db.info["engine"], "before_cursor_execute", listener)
    assert statements == []
    assert _counts(db) == before_counts
    assert (closing.status, closing.state_revision) == before_state


def test_live_operational_contribution_changes_do_not_change_statement(db):
    closing, _review, _snapshot = _seed(db)
    before = get_cycle_annual_closing_statement(db, closing_id=closing.id)
    db.execute(
        text("UPDATE contributions SET amount='999.00' WHERE id=1"),
    )
    after = get_cycle_annual_closing_statement(db, closing_id=closing.id)
    assert before == after


def test_payout_obligation_verification_is_optional_and_read_only(db):
    closing, _review, _snapshot = _seed(db)
    without_check = get_cycle_annual_closing_statement(db, closing_id=closing.id)
    assert without_check.payout_obligations_verified is None

    materialize_cycle_annual_closing_payout_obligations(db, closing_id=closing.id)
    before = _counts(db)
    verified = get_cycle_annual_closing_statement(
        db, closing_id=closing.id, verify_payout_obligations=True,
    )
    assert verified.payout_obligations_verified is True
    assert _counts(db) == before


def test_required_missing_payout_obligations_fail_without_materialization(db):
    closing, _review, _snapshot = _seed(db)
    before = _counts(db)
    with pytest.raises(ClosingStatementError) as caught:
        get_cycle_annual_closing_statement(
            db, closing_id=closing.id, verify_payout_obligations=True,
        )
    assert caught.value.reason_code == "OBLIGATIONS_INCOMPLETE"
    assert _counts(db) == before


@pytest.mark.parametrize("divergence", ["one_cent", "extra"], ids=["one-cent", "extra-row"])
def test_required_divergent_payout_obligations_fail_closed(db, divergence):
    closing, _review, snapshot = _seed(db)
    helpers = _obligation_helpers()
    if divergence == "one_cent":
        helpers._manual_obligation(db, snapshot, closing, amount=D("99.99"))
        helpers._manual_obligation(
            db, snapshot, closing, member_id=2, participation_id=2,
            amount=ZERO, source_hash=snapshot.payload_hash,
        )
    else:
        db.add(User(id=5, name="Member Three", email="member3@statement.test", cpf="00000000000005", password_hash="unused"))
        db.flush()
        db.add(Member(id=3, user_id=5, group_id=1))
        db.add(CycleParticipation(id=3, cycle_id=1, member_id=3, status="ACTIVE"))
        db.flush()
        helpers._manual_obligation(
            db, snapshot, closing, member_id=3, participation_id=3,
            amount=ZERO, source_hash=snapshot.payload_hash,
        )
    before = _counts(db)
    with pytest.raises(ClosingStatementError) as caught:
        get_cycle_annual_closing_statement(
            db, closing_id=closing.id, verify_payout_obligations=True,
        )
    assert caught.value.reason_code in {"OBLIGATIONS_INCOMPLETE", "OBLIGATION_RECONCILIATION_MISMATCH"}
    assert _counts(db) == before


def test_unknown_participant_is_a_deterministic_error(db):
    closing, _review, _snapshot = _seed(db)
    with pytest.raises(ClosingStatementError) as caught:
        get_cycle_annual_closing_participant_statement(
            db, closing_id=closing.id, member_id=999,
        )
    assert caught.value.reason_code == "PARTICIPANT_NOT_FOUND"


@pytest.mark.parametrize(
    "tamper, reason",
    [
        ("payload_hash", "SNAPSHOT_INVALID"),
        ("calculation_hash", "SNAPSHOT_INVALID"),
        ("gross_share_cent", "SNAPSHOT_INVALID"),
        ("eligible_contribution_cent", "SNAPSHOT_INVALID"),
        ("projected_net_cent", "SNAPSHOT_INVALID"),
        ("duplicate_participant", "SNAPSHOT_INVALID"),
        ("malformed_obligation", "SNAPSHOT_INVALID"),
        ("float", "SNAPSHOT_INVALID"),
    ],
)
def test_snapshot_tampering_fails_closed(db, tamper, reason):
    closing, review, snapshot = _seed(db)
    if tamper == "payload_hash":
        snapshot.payload_hash = "f" * 64
    elif tamper == "calculation_hash":
        payload = json.loads(snapshot.canonical_payload)
        payload["calculation_hash"] = "f" * 64
        snapshot.canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        snapshot.payload_hash = snapshot_payload_hash(snapshot.canonical_payload)
    elif tamper == "gross_share_cent":
        _rewrite_snapshot(snapshot, lambda payload: payload["participants"][0].update(gross_share="0.01"))
    elif tamper == "eligible_contribution_cent":
        _rewrite_snapshot(snapshot, lambda payload: payload["participants"][0].update(eligible_contributions="100.01"))
    elif tamper == "projected_net_cent":
        _rewrite_snapshot(snapshot, lambda payload: payload["participants"][0].update(projected_net="99.99"))
    elif tamper == "duplicate_participant":
        def duplicate_zero_participant(payload):
            payload["participants"].append(payload["participants"][1].copy())
        _rewrite_snapshot(snapshot, duplicate_zero_participant)
    elif tamper == "malformed_obligation":
        _rewrite_snapshot(snapshot, lambda payload: payload["participants"][0]["compensable_obligations"].append({
            "source_id": "bad-kind", "kind": "UNKNOWN", "amount": "0.00",
            "due_date": "2027-12-10", "loan_id": None, "agreement_id": None,
        }))
    else:
        payload = json.loads(snapshot.canonical_payload)
        payload["gross_realized_result"] = 0.0
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        snapshot.canonical_payload = raw
        snapshot.payload_hash = snapshot_payload_hash(raw)

    if tamper in {"projected_net_cent", "duplicate_participant", "malformed_obligation"}:
        review.calculation_hash = json.loads(snapshot.canonical_payload)["calculation_hash"]
        _resign_review(review)

    with pytest.raises(ClosingStatementError) as caught:
        get_cycle_annual_closing_statement(db, closing_id=closing.id)
    assert caught.value.reason_code == reason


@pytest.mark.parametrize(
    "tamper, reason",
    [
        ("review_hash", "REVIEW_INVALID"),
        ("reconciliation_hash", "REVIEW_INVALID"),
        ("cycle_id", "REVIEW_INVALID"),
        ("liability_cent", "REVIEW_INVALID"),
    ],
)
def test_review_tampering_fails_closed(db, tamper, reason):
    closing, review, _snapshot = _seed(db)
    if tamper == "review_hash":
        review.review_hash = "0" * 64
    elif tamper == "reconciliation_hash":
        review.reconciliation_hash = "0" * 64
    elif tamper == "cycle_id":
        review.cycle_id = 999
    else:
        review.participant_payout_liability = D("99.99")
        _resign_review(review)
    with pytest.raises(ClosingStatementError) as caught:
        get_cycle_annual_closing_statement(db, closing_id=closing.id)
    assert caught.value.reason_code == reason


@pytest.mark.parametrize("mismatch", ["cutoff", "calculation_hash"])
def test_snapshot_review_cutoff_and_calculation_hash_must_match(db, mismatch):
    closing, review, snapshot = _seed(db)
    if mismatch == "cutoff":
        review.closing_cutoff_at += timedelta(minutes=1)
    else:
        review.calculation_hash = "0" * 64
    _resign_review(review)
    with pytest.raises(ClosingStatementError) as caught:
        get_cycle_annual_closing_statement(db, closing_id=closing.id)
    assert caught.value.reason_code in {"SNAPSHOT_REVIEW_MISMATCH", "REVIEW_INVALID"}
