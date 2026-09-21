import os
import subprocess
import sys
import threading
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app.models import (
    AgreementInstallment,
    CollectionAgreement,
    CollectionCase,
    Group,
    Loan,
    LoanInstallment,
    Member,
    Notification,
    User,
)
from app.services import agreements_v039
from app.services.agreements_v039 import decide_agreement


BASE_REVISION = "0092_versioned_late_charge_foundation"
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
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
        dbapi_connection.execute("PRAGMA journal_mode=WAL")
        dbapi_connection.execute("PRAGMA busy_timeout=30000")

    return engine


def _seed(engine):
    sessions = sessionmaker(bind=engine)
    db = sessions()
    user = User(
        name="Agreement concurrency",
        email="agreement-concurrency@example.invalid",
        cpf="agreement-concurrency-cpf",
        password_hash="hash",
        role="ADMIN",
    )
    group = Group(name="Agreement concurrency group", max_installments=6)
    db.add_all([user, group])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id, status="ACTIVE")
    db.add(member)
    db.flush()
    loan = Loan(
        member_id=member.id,
        principal=Decimal("100.00"),
        principal_settled_with_own_balance=Decimal("0.00"),
        monthly_rate=Decimal("0.01"),
        installments=1,
        status="OVERDUE",
    )
    db.add(loan)
    db.flush()
    loan_installment = LoanInstallment(
        loan_id=loan.id,
        number=1,
        due_date=date.today() - timedelta(days=10),
        principal=Decimal("100.00"),
        interest=Decimal("0.00"),
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        paid_penalty_amount=Decimal("0.00"),
        penalty_amount=Decimal("0.00"),
        status="OVERDUE",
    )
    db.add(loan_installment)
    db.flush()
    agreement = CollectionAgreement(
        loan_id=loan.id,
        member_id=member.id,
        requested_by=user.id,
        status="REQUESTED",
        installments=1,
        total_amount=Decimal("100.00"),
        snapshot="{}",
    )
    db.add(agreement)
    db.flush()
    case = CollectionCase(
        member_id=member.id,
        loan_id=loan.id,
        status="OPEN",
        stage="SOFT",
        opened_at=loan.requested_at,
        next_action_at=loan.requested_at + timedelta(days=1),
    )
    db.add(case)
    db.commit()
    ids = {"agreement": agreement.id, "admin": user.id, "loan": loan.id}
    db.close()
    return sessions, ids


def _run_race(engine, approve_first, second_decision):
    sessions = sessionmaker(bind=engine)
    barrier = threading.Barrier(2)
    results = []
    lock = threading.Lock()
    original_claim = agreements_v039._claim_collection_agreement

    def controlled_claim(db, agreement_id, expected_revision, status, decided_by, decided_at):
        barrier.wait(timeout=30)
        return original_claim(db, agreement_id, expected_revision, status, decided_by, decided_at)

    agreements_v039._claim_collection_agreement = controlled_claim

    def worker(name, approve):
        db = sessions()
        row = {"name": name, "approve": approve}
        try:
            agreement = db.get(CollectionAgreement, 1)
            row["saw_requested"] = agreement.status == "REQUESTED"
            decided = decide_agreement(db, 1, 1, approve, name)
            row["returned"] = decided.status
            db.commit()
            row["commit"] = "OK"
        except Exception as exc:
            row["exception"] = type(exc).__name__
            row["message"] = str(exc)
            db.rollback()
            row["rollback"] = "OK"
        finally:
            db.close()
            with lock:
                results.append(row)

    try:
        threads = [
            threading.Thread(
                target=worker,
                args=("first", approve_first),
                name="first",
            ),
            threading.Thread(
                target=worker,
                args=("second", second_decision),
                name="second",
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert all(not thread.is_alive() for thread in threads)
    finally:
        agreements_v039._claim_collection_agreement = original_claim
    return results


def _snapshot(engine, ids):
    with engine.connect() as connection:
        return {
            "agreement": connection.execute(
                text("SELECT status, state_revision FROM collection_agreements WHERE id = :id"),
                {"id": ids["agreement"]},
            ).one(),
            "loan": connection.execute(
                text("SELECT status, state_revision FROM loans WHERE id = :id"),
                {"id": ids["loan"]},
            ).one(),
            "agreement_installments": connection.execute(
                text("SELECT COUNT(*) FROM agreement_installments WHERE agreement_id = :id"),
                {"id": ids["agreement"]},
            ).scalar_one(),
            "agreed_loan_installments": connection.execute(
                text("SELECT COUNT(*) FROM loan_installments WHERE loan_id = :id AND status = 'AGREED'"),
                {"id": ids["loan"]},
            ).scalar_one(),
            "resolved_cases": connection.execute(
                text("SELECT COUNT(*) FROM collection_cases WHERE loan_id = :id AND status = 'RESOLVED'"),
                {"id": ids["loan"]},
            ).scalar_one(),
            "audits": connection.execute(
                text("SELECT COUNT(*) FROM audit_logs WHERE entity_type = 'COLLECTION_AGREEMENT' AND entity_id = :id"),
                {"id": str(ids["agreement"])},
            ).scalar_one(),
            "notifications": connection.execute(
                text("SELECT COUNT(*) FROM notifications WHERE reference_type = 'COLLECTION_AGREEMENT' AND reference_id = :id"),
                {"id": str(ids["agreement"])},
            ).scalar_one(),
        }


def test_approve_reject_race_documents_stale_agreement_effects(tmp_path):
    """Only the claimed decision may execute effects."""
    database = tmp_path / "agreement_concurrency.db"
    _upgrade(database)
    engine = _engine(database)
    _sessions, ids = _seed(engine)
    results = _run_race(engine, True, False)
    snapshot = _snapshot(engine, ids)

    assert len(results) == 2
    assert all(row.get("saw_requested") is True for row in results)
    assert sum(row.get("commit") == "OK" for row in results) == 1
    assert sum(row.get("exception") == "ValueError" for row in results) == 1
    loser = next(row for row in results if row.get("exception") == "ValueError")
    assert "Acordo não encontrado ou já decidido." in loser["message"]
    assert snapshot["agreement"].status in {"APPROVED", "REJECTED"}
    assert snapshot["agreement"].state_revision == 1
    assert snapshot["audits"] == 1
    assert snapshot["notifications"] == 1
    if next(row for row in results if row.get("commit") == "OK")["approve"]:
        assert snapshot["agreement"].status == "APPROVED"
        assert snapshot["loan"].status == "RESTRUCTURED"
        assert snapshot["agreement_installments"] == 1
        assert snapshot["agreed_loan_installments"] == 1
        assert snapshot["resolved_cases"] == 1
    else:
        assert snapshot["agreement"].status == "REJECTED"
        assert snapshot["agreement_installments"] == 0
        assert snapshot["agreed_loan_installments"] == 0
        assert snapshot["resolved_cases"] == 0


def test_approve_approve_race_hits_installment_unique(tmp_path):
    database = tmp_path / "agreement_approve_approve.db"
    _upgrade(database)
    engine = _engine(database)
    _sessions, ids = _seed(engine)
    results = _run_race(engine, True, True)
    snapshot = _snapshot(engine, ids)

    assert len(results) == 2
    assert all(row.get("saw_requested") is True for row in results)
    assert sum(row.get("commit") == "OK" for row in results) == 1
    assert sum(row.get("exception") == "ValueError" for row in results) == 1
    loser = next(row for row in results if row.get("exception") == "ValueError")
    assert "Acordo não encontrado ou já decidido." in loser["message"]
    assert snapshot["agreement"].status == "APPROVED"
    assert snapshot["agreement"].state_revision == 1
    assert snapshot["agreement_installments"] == 1
    assert snapshot["agreed_loan_installments"] == 1
    assert snapshot["audits"] == 1
    assert snapshot["notifications"] == 1


def test_claim_failure_rolls_back_all_decision_effects(tmp_path, monkeypatch):
    database = tmp_path / "agreement_claim_rollback.db"
    _upgrade(database)
    engine = _engine(database)
    _sessions, ids = _seed(engine)
    db = sessionmaker(bind=engine)()

    def fail_notification(*args, **kwargs):
        raise RuntimeError("forced post-claim failure")

    monkeypatch.setattr(agreements_v039, "create_notification", fail_notification)
    with pytest.raises(RuntimeError, match="forced post-claim failure"):
        decide_agreement(db, ids["agreement"], ids["admin"], False, "forced failure")
    db.rollback()
    db.close()

    snapshot = _snapshot(engine, ids)
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT status, state_revision, decided_by, decided_at FROM collection_agreements WHERE id = :id"),
            {"id": ids["agreement"]},
        ).one()
    assert row.status == "REQUESTED"
    assert row.state_revision == 0
    assert row.decided_by is None
    assert row.decided_at is None
    assert snapshot["agreement_installments"] == 0
    assert snapshot["audits"] == 0
    assert snapshot["notifications"] == 0
    assert snapshot["resolved_cases"] == 0


def test_stale_agreement_rowcount_zero_has_no_side_effects(tmp_path):
    database = tmp_path / "agreement_stale_claim.db"
    _upgrade(database)
    engine = _engine(database)
    sessions, ids = _seed(engine)
    stale_db = sessions()
    winner_db = sessions()
    stale = stale_db.get(CollectionAgreement, ids["agreement"])
    assert stale.status == "REQUESTED"
    decide_agreement(winner_db, ids["agreement"], ids["admin"], False, "winner")
    winner_db.commit()
    with pytest.raises(ValueError, match="já decidido"):
        decide_agreement(stale_db, ids["agreement"], ids["admin"], True, "loser")
    stale_db.rollback()
    stale_db.close()
    winner_db.close()

    snapshot = _snapshot(engine, ids)
    assert snapshot["agreement"].status == "REJECTED"
    assert snapshot["agreement_installments"] == 0
    assert snapshot["audits"] == 1
    assert snapshot["notifications"] == 1


def test_reject_reject_race_persists_duplicate_effects(tmp_path):
    database = tmp_path / "agreement_reject_reject.db"
    _upgrade(database)
    engine = _engine(database)
    _sessions, ids = _seed(engine)
    results = _run_race(engine, False, False)
    snapshot = _snapshot(engine, ids)

    assert len(results) == 2
    assert all(row.get("saw_requested") is True for row in results)
    assert sum(row.get("commit") == "OK" for row in results) == 1
    assert sum(row.get("exception") == "ValueError" for row in results) == 1
    loser = next(row for row in results if row.get("exception") == "ValueError")
    assert "Acordo não encontrado ou já decidido." in loser["message"]
    assert snapshot["agreement"].status == "REJECTED"
    assert snapshot["agreement"].state_revision == 1
    assert snapshot["agreement_installments"] == 0
    assert snapshot["audits"] == 1
    assert snapshot["notifications"] == 1


def test_serial_decision_second_call_is_rejected(tmp_path):
    database = tmp_path / "agreement_serial.db"
    _upgrade(database)
    engine = _engine(database)
    _sessions, ids = _seed(engine)
    db = sessionmaker(bind=engine)()
    decide_agreement(db, ids["agreement"], ids["admin"], True, "first")
    db.commit()
    assert db.get(CollectionAgreement, ids["agreement"]).state_revision == 1
    with pytest.raises(ValueError, match="já decidido"):
        decide_agreement(db, ids["agreement"], ids["admin"], False, "second")
    db.rollback()
    db.close()


def test_serial_reject_second_call_is_rejected(tmp_path):
    database = tmp_path / "agreement_serial_reject.db"
    _upgrade(database)
    engine = _engine(database)
    _sessions, ids = _seed(engine)
    db = sessionmaker(bind=engine)()
    decide_agreement(db, ids["agreement"], ids["admin"], False, "first")
    db.commit()
    assert db.get(CollectionAgreement, ids["agreement"]).state_revision == 1
    with pytest.raises(ValueError, match="já decidido"):
        decide_agreement(db, ids["agreement"], ids["admin"], False, "second")
    db.rollback()
    db.close()
