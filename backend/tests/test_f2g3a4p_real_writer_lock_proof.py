"""F2-G3A4P: PostgreSQL proof using real loan PIX and settlement services.

The barriers wrap only the service callback boundary. Production SELECT FOR
UPDATE statements, member/account locks, flushes, and transaction ownership all
remain active and are issued by the real services.
"""

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Group, Loan, LoanInstallment, Member, MemberFinancialAccount, Payment, User
from app.services.loan_installment_pix_attempts import approve_or_reconcile, _snapshot
from app.services.payment_settlement import settle_confirmed_pix_payment
from app.core.pix_attempt_v1 import canonical_json, financial_date, local_expiry_utc, snapshot_hash


def _postgres_url():
    try:
        url = sa.engine.make_url(os.environ.get("DATABASE_URL", ""))
    except Exception:
        return None
    if (
        os.environ.get("APP_ENV") != "test"
        or not url.drivername.startswith("postgresql")
        or url.host not in {"127.0.0.1", "localhost"}
        or url.port != 5432
        or url.database != "frcaixinha_test"
        or url.username != "frcaixinha_test"
    ):
        return None
    return url


@pytest.fixture
def real_writer_db(monkeypatch):
    url = _postgres_url()
    if url is None:
        pytest.skip("requires isolated PostgreSQL 16 frcaixinha_test CI service")
    engine = sa.create_engine(
        url, pool_size=6,
        connect_args={"options": "-c statement_timeout=20000 -c lock_timeout=12000"},
    )
    schema = "f2g3a4p_" + uuid.uuid4().hex
    try:
        with engine.begin() as connection:
            assert int(connection.exec_driver_sql("SHOW server_version_num").scalar_one()) // 10000 == 16
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        isolated = engine.execution_options(schema_translate_map={None: schema})
        with isolated.begin() as connection:
            Base.metadata.create_all(connection)
        # The normal PIX late-charge configuration is intentionally held fixed
        # for this lock-order test; the production snapshot/obligation code runs.
        monkeypatch.setattr("app.services.loan_installment_pix_attempts.settings.loan_late_charge_effective_date", None)
        yield isolated
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        engine.dispose()


def _seed_loan(engine):
    token = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    day = financial_date(now)
    with Session(engine, autoflush=False) as db:
        group = Group(name="F2G3A4P-" + token)
        user = User(
            name="F2G3A4P", email=token + "@f2g3a4p.test",
            cpf=token[:14], password_hash="test", role="USER", is_active=True,
        )
        db.add_all([group, user])
        db.flush()
        member = Member(user_id=user.id, group_id=group.id, status="ACTIVE")
        db.add(member)
        db.flush()
        db.add(MemberFinancialAccount(member_id=member.id))
        loan = Loan(
            member_id=member.id, principal=Decimal("100.00"),
            monthly_rate=Decimal("0.20"), installments=1, status="ACTIVE",
        )
        db.add(loan)
        db.flush()
        installment = LoanInstallment(
            loan_id=loan.id, number=1, due_date=date(2099, 1, 1),
            principal=Decimal("100.00"), interest=Decimal("20.00"),
            amount=Decimal("120.00"), paid_amount=Decimal("0.00"),
            penalty_amount=Decimal("0.00"), paid_penalty_amount=Decimal("0.00"),
            status="OPEN",
        )
        db.add(installment)
        db.flush()
        snapshot = _snapshot(db, loan, installment, day)
        approval_payment = Payment(
            provider="mercado_pago", provider_payment_id="approval-" + token,
            idempotency_key="approval-key-" + token, amount=Decimal("120.00"),
            amount_received=Decimal("120.00"), status="PENDING", raw_status="PENDING",
            reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id),
            attempt_status="PENDING", calculated_for_date=day,
            expires_at=local_expiry_utc(day), financial_snapshot_json=canonical_json(snapshot),
            snapshot_hash=snapshot_hash(snapshot),
        )
        settlement_payment = Payment(
            provider="mercado_pago", provider_payment_id="settlement-" + token,
            idempotency_key="settlement-key-" + token, amount=Decimal("120.00"),
            amount_received=Decimal("120.00"), status="approved", raw_status="approved",
            confirmed_at=now, reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id),
        )
        db.add_all([approval_payment, settlement_payment])
        db.commit()
        return loan.id, installment.id, approval_payment.id, settlement_payment.id, now


def _pid(db):
    return db.connection().connection.driver_connection.info.backend_pid


def _await_wait(engine, pid):
    # Polling observes PostgreSQL's lock table; Events establish ordering.
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            queued = connection.execute(sa.text(
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=:pid AND NOT granted)"
            ), {"pid": pid}).scalar_one()
        if queued:
            return
        time.sleep(0.01)
    pytest.fail(f"backend {pid} never queued on a PostgreSQL lock")


def _sqlstate(exc):
    return getattr(getattr(exc.orig, "diag", None), "sqlstate", None)


class TestF2G3A4PRealWriterLockProof:
    def test_real_loan_pix_approval_and_settlement_reproduce_member_installment_cycle(self, real_writer_db):
        """Run approve_or_reconcile and a competing settlement without lock mocks."""
        engine = real_writer_db
        _loan_id, _installment_id, approval_id, settlement_id, confirmed_at = _seed_loan(engine)
        approval_at_callback = threading.Event()
        release_approval = threading.Event()
        competitor_started = threading.Event()
        pids = {}

        def approval_transaction():
            with Session(engine, autoflush=False) as db:
                pids["approval"] = _pid(db)
                payment = db.get(Payment, approval_id)

                def real_settlement_callback(session, payment, **kwargs):
                    # approve_or_reconcile has already locked LoanInstallment.
                    approval_at_callback.set()
                    assert release_approval.wait(timeout=15)
                    return settle_confirmed_pix_payment(session, payment, **kwargs)

                try:
                    result = approve_or_reconcile(
                        db, payment, confirmed_at,
                        {"id": payment.provider_payment_id, "status": "approved"},
                        real_settlement_callback,
                    )
                    db.commit()
                    return ("completed", result.id if result else None)
                except DBAPIError as exc:
                    state = _sqlstate(exc)
                    db.rollback()
                    if state == "40P01":
                        return ("deadlock", state)
                    raise

        def competing_settlement():
            with Session(engine, autoflush=False) as db:
                pids["settlement"] = _pid(db)
                competitor_started.set()
                payment = db.get(Payment, settlement_id)
                try:
                    result = settle_confirmed_pix_payment(
                        db, payment, confirmation_source="TEST",
                        confirmed_at=confirmed_at,
                        remote_payload={"id": payment.provider_payment_id, "status": "approved"},
                    )
                    db.commit()
                    return ("completed", result.id if result else None)
                except DBAPIError as exc:
                    state = _sqlstate(exc)
                    db.rollback()
                    if state == "40P01":
                        return ("deadlock", state)
                    raise

        with ThreadPoolExecutor(max_workers=2) as pool:
            approval = pool.submit(approval_transaction)
            assert approval_at_callback.wait(timeout=15)
            settlement = pool.submit(competing_settlement)
            try:
                assert competitor_started.wait(timeout=10)
                _await_wait(engine, pids["settlement"])
                assert not settlement.done()
            finally:
                release_approval.set()
            outcomes = [approval.result(timeout=20), settlement.result(timeout=20)]

        assert sum(outcome[0] == "deadlock" for outcome in outcomes) == 1, outcomes
        assert next(outcome[1] for outcome in outcomes if outcome[0] == "deadlock") == "40P01"
