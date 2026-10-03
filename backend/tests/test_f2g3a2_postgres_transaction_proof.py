"""PostgreSQL 16 proof of member-lock ordering and transaction visibility.

The payment side uses the production member lock and persists a Payment plus
the corresponding Contribution state. No temporal writer is enabled here.
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
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    Contribution, Cycle, Group, Member, MemberFinancialAccount, Payment, User,
)
from app.services.member_financial import lock_member_financial_account


def _safe_postgres_url():
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
def proof():
    url = _safe_postgres_url()
    if url is None:
        pytest.skip("requires isolated PostgreSQL 16 frcaixinha_test CI service")
    engine = sa.create_engine(
        url, pool_size=5, connect_args={"options": "-c statement_timeout=15000"},
    )
    schema = "f2g3a2_" + uuid.uuid4().hex
    try:
        with engine.begin() as connection:
            assert int(connection.exec_driver_sql("SHOW server_version_num").scalar_one()) // 10000 == 16
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        isolated = engine.execution_options(schema_translate_map={None: schema})
        tables = [
            User.__table__, Group.__table__, Member.__table__, Cycle.__table__,
            MemberFinancialAccount.__table__, Payment.__table__, Contribution.__table__,
        ]
        decisions = sa.Table(
            "f2g3a2_decisions", sa.MetaData(),
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("seen_paid", sa.Numeric(14, 2), nullable=False),
        )
        with isolated.begin() as connection:
            Base.metadata.create_all(connection, tables=tables)
            decisions.create(connection)
        token = uuid.uuid4().hex
        with Session(isolated, autoflush=False) as db:
            group = Group(name="F2G3A2-" + token)
            user = User(
                name="F2G3A2", email=token + "@f2g3a2.test",
                cpf=token[:14], password_hash="test",
            )
            db.add_all([group, user])
            db.flush()
            member = Member(user_id=user.id, group_id=group.id)
            db.add(member)
            db.flush()
            contribution = Contribution(
                member_id=member.id, cycle_id=None, competence=date(2026, 10, 1),
                due_date=date(2026, 10, 10), amount=Decimal("100.00"),
                paid_amount=Decimal("0.00"), status="PENDING",
            )
            db.add_all([MemberFinancialAccount(member_id=member.id), contribution])
            db.flush()
            member_id, contribution_id = member.id, contribution.id
            db.commit()
        yield isolated, decisions, member_id, contribution_id
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        engine.dispose()


def _payment_commit(engine, member_id, contribution_id, ready=None, release=None):
    with Session(engine, autoflush=False) as db:
        lock_member_financial_account(db, member_id)
        contribution = db.get(Contribution, contribution_id)
        payment = Payment(
            provider="test", provider_payment_id=uuid.uuid4().hex,
            idempotency_key=uuid.uuid4().hex, amount=Decimal("40.00"),
            amount_received=Decimal("40.00"), status="approved",
            raw_status="approved", confirmed_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
            reference_type="CONTRIBUTION", reference_id=str(contribution_id),
        )
        db.add(payment)
        db.flush()
        contribution.paid_amount = Decimal("40.00")
        contribution.status = "PARTIAL"
        contribution.payment_id = payment.id
        db.flush()
        if ready is not None:
            ready.set()
            assert release.wait(timeout=12)
        db.commit()


def _financial_state(db, contribution_id):
    paid = db.query(Contribution.paid_amount).filter(
        Contribution.id == contribution_id,
    ).scalar()
    payments = db.query(Payment).filter(
        Payment.reference_type == "CONTRIBUTION",
        Payment.reference_id == str(contribution_id),
    ).count()
    return paid, payments


def _backend_pid_without_query(db):
    # Fetching the DBAPI connection does not issue a PostgreSQL SELECT.
    return db.connection().connection.driver_connection.info.backend_pid


def _await_lock_wait(engine, pid):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with engine.connect() as probe:
            waiting = probe.execute(sa.text(
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=:pid AND NOT granted)"
            ), {"pid": pid}).scalar_one()
        if waiting:
            return
        time.sleep(0.01)  # Observation only; Events and row locks set the order.
    pytest.fail(f"backend {pid} did not wait for a PostgreSQL lock")


def _worker(engine, decisions, member_id, contribution_id, isolation,
            prelock_select, ready=None):
    with Session(engine.execution_options(isolation_level=isolation), autoflush=False) as db:
        pid = _backend_pid_without_query(db)
        if prelock_select:
            db.execute(sa.text("SELECT 1"))
        if ready is not None:
            ready(pid)
        lock_member_financial_account(db, member_id)
        state = _financial_state(db, contribution_id)
        db.execute(decisions.insert().values(seen_paid=state[0]))
        db.commit()
        return state


class TestF2G3A2PostgresTransactionProof:
    @pytest.mark.parametrize("isolation,prelock_select,expected", [
        ("READ COMMITTED", True, (Decimal("40.00"), 1)),
        ("REPEATABLE READ", True, (Decimal("40.00"), 1)),
        ("REPEATABLE READ", False, (Decimal("40.00"), 1)),
    ])
    def test_payment_committed_before_member_lock(self, proof, isolation, prelock_select, expected):
        engine, decisions, member_id, contribution_id = proof
        _payment_commit(engine, member_id, contribution_id)
        assert _worker(engine, decisions, member_id, contribution_id,
                       isolation, prelock_select) == expected

    @pytest.mark.parametrize("isolation,prelock_select,expected", [
        ("READ COMMITTED", True, (Decimal("40.00"), 1)),
        ("REPEATABLE READ", True, (Decimal("0.00"), 0)),
        ("REPEATABLE READ", False, (Decimal("0.00"), 0)),
    ])
    def test_payment_committed_while_worker_waits_for_member_lock(
        self, proof, isolation, prelock_select, expected,
    ):
        engine, decisions, member_id, contribution_id = proof
        payment_ready = threading.Event()
        release_payment = threading.Event()
        worker_ready = threading.Event()
        worker_pid = []

        def announce(pid):
            worker_pid.append(pid)
            worker_ready.set()

        with ThreadPoolExecutor(max_workers=2) as pool:
            payment = pool.submit(
                _payment_commit, engine, member_id, contribution_id,
                payment_ready, release_payment,
            )
            try:
                assert payment_ready.wait(timeout=12)
                worker = pool.submit(
                    _worker, engine, decisions, member_id, contribution_id,
                    isolation, prelock_select, announce,
                )
                assert worker_ready.wait(timeout=12)
                _await_lock_wait(engine, worker_pid[0])
                assert not worker.done()
            finally:
                release_payment.set()
            payment.result(timeout=20)
            assert worker.result(timeout=20) == expected

    @pytest.mark.parametrize("isolation", ["READ COMMITTED", "REPEATABLE READ"])
    def test_payment_committed_after_worker_lock_is_ordered_after_decision(self, proof, isolation):
        engine, decisions, member_id, contribution_id = proof
        payment_started = threading.Event()
        payment_pid = []
        with Session(engine.execution_options(isolation_level=isolation), autoflush=False) as worker:
            lock_member_financial_account(worker, member_id)
            assert _financial_state(worker, contribution_id) == (Decimal("0.00"), 0)

            def pay():
                with Session(engine, autoflush=False) as db:
                    payment_pid.append(_backend_pid_without_query(db))
                    payment_started.set()
                    lock_member_financial_account(db, member_id)
                    contribution = db.get(Contribution, contribution_id)
                    contribution.paid_amount = Decimal("40.00")
                    contribution.status = "PARTIAL"
                    db.commit()
                    return Decimal("40.00")

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(pay)
                try:
                    assert payment_started.wait(timeout=12)
                    _await_lock_wait(engine, payment_pid[0])
                    assert not future.done()
                    worker.execute(decisions.insert().values(seen_paid=Decimal("0.00")))
                finally:
                    worker.commit()
                assert future.result(timeout=20) == Decimal("40.00")
        with Session(engine) as db:
            assert db.execute(sa.select(decisions.c.seen_paid)).scalar_one() == Decimal("0.00")
            assert _financial_state(db, contribution_id)[0] == Decimal("40.00")

    def test_repeatable_read_own_flush_and_rollback(self, proof):
        engine, _decisions, member_id, contribution_id = proof
        with Session(engine.execution_options(isolation_level="REPEATABLE READ"), autoflush=False) as db:
            lock_member_financial_account(db, member_id)
            contribution = db.get(Contribution, contribution_id)
            contribution.paid_amount = Decimal("10.00")
            db.flush()
            db.expire_all()
            assert _financial_state(db, contribution_id) == (Decimal("10.00"), 0)
            db.rollback()
        with Session(engine) as observer:
            assert _financial_state(observer, contribution_id) == (Decimal("0.00"), 0)
            lock_member_financial_account(observer, member_id)
            observer.rollback()

    def test_writer_without_member_lock_can_overtake_a_decision(self, proof):
        engine, decisions, member_id, contribution_id = proof
        with Session(engine, autoflush=False) as worker:
            lock_member_financial_account(worker, member_id)
            assert _financial_state(worker, contribution_id) == (Decimal("0.00"), 0)

            def uncoordinated_write():
                with Session(engine, autoflush=False) as db:
                    db.execute(
                        sa.update(Contribution).where(Contribution.id == contribution_id)
                        .values(paid_amount=Decimal("40.00"), status="PARTIAL")
                    )
                    db.commit()

            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(uncoordinated_write).result(timeout=20)
            worker.execute(decisions.insert().values(seen_paid=Decimal("0.00")))
            worker.commit()
        with Session(engine) as observer:
            assert observer.execute(sa.select(decisions.c.seen_paid)).scalar_one() == Decimal("0.00")
            assert _financial_state(observer, contribution_id)[0] == Decimal("40.00")
