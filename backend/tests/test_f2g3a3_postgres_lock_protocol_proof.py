"""F2-G3A3 PostgreSQL lock-order proofs.

These tests exercise PostgreSQL row-lock behavior using the production
Member -> MemberFinancialAccount helper and test-only rows standing for the
obligation rows named in the source lock paths. They do not call or alter
financial writers.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from test_f2g3a2_postgres_transaction_proof import proof
from app.services.member_financial import lock_member_financial_account


def _probe_table(engine):
    table = sa.Table(
        "f2g3a3_lock_probe", sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("value", sa.Integer, nullable=False),
    )
    table.create(engine)
    with engine.begin() as connection:
        connection.execute(table.insert(), [{"id": 1, "value": 0}, {"id": 2, "value": 0}])
    return table


def _drop_probe(engine, table):
    table.drop(engine)


def _pid(db):
    return db.connection().connection.driver_connection.info.backend_pid


def _await_waiting(engine, pid):
    import time

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            waiting = connection.execute(sa.text(
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=:pid AND NOT granted)"
            ), {"pid": pid}).scalar_one()
        if waiting:
            return
        time.sleep(0.01)  # Observation only; Events and PostgreSQL locks order the test.
    pytest.fail(f"backend {pid} did not wait for a PostgreSQL lock")


def _await_both_waiting(engine, first_pid, second_pid):
    import time

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            count = connection.execute(sa.text(
                "SELECT count(*) FROM pg_locks WHERE NOT granted "
                "AND pid IN (:first_pid, :second_pid)"
            ), {"first_pid": first_pid, "second_pid": second_pid}).scalar_one()
        if count == 2:
            return
        time.sleep(0.01)  # Observe the lock graph; Events establish test ordering.
    pytest.fail("both transactions did not enter the expected PostgreSQL lock cycle")


def _create_second_member(engine):
    import uuid

    from app.models import Group, Member, MemberFinancialAccount, User

    token = uuid.uuid4().hex
    with Session(engine, autoflush=False) as db:
        group = db.query(Group).first()
        user = User(name="F2G3A3", email=token + "@f2g3a3.test",
                    cpf=token[:14], password_hash="test")
        db.add(user)
        db.flush()
        member = Member(user_id=user.id, group_id=group.id)
        db.add(member)
        db.flush()
        db.add(MemberFinancialAccount(member_id=member.id))
        db.flush()
        member_id = member.id
        db.commit()
    return member_id


class TestF2G3A3PostgresLockProtocolProof:
    @pytest.mark.parametrize("path", ["agreement-settlement", "agreement-reversal"])
    def test_obligation_then_member_inverts_member_first_protocol(self, proof, path):
        """An obligation-first path deadlocks if it later requests Member.

        Agreement settlement/reversal currently lock Agreement then
        AgreementInstallment and do not request Member. This test proves why
        adding Member only after those locks is unsafe; it is not a claim that
        the current writer already requests Member.
        """
        engine, _decisions, member_id, _contribution_id = proof
        probe = _probe_table(engine)
        obligation_has_lock = threading.Event()
        member_has_lock = threading.Event()
        pids = {}

        def obligation_first():
            try:
                with Session(engine, autoflush=False) as db:
                    pids["obligation"] = _pid(db)
                    db.execute(sa.select(probe).where(probe.c.id == 1).with_for_update()).one()
                    obligation_has_lock.set()
                    assert member_has_lock.wait(timeout=10)
                    lock_member_financial_account(db, member_id)
                    db.rollback()
                    return "completed"
            except DBAPIError as exc:
                dbcode = getattr(getattr(exc.orig, "diag", None), "sqlstate", None)
                if dbcode == "40P01":
                    return "deadlock"
                raise

        def member_first():
            try:
                assert obligation_has_lock.wait(timeout=10)
                with Session(engine, autoflush=False) as db:
                    pids["member"] = _pid(db)
                    lock_member_financial_account(db, member_id)
                    member_has_lock.set()
                    db.execute(sa.select(probe).where(probe.c.id == 1).with_for_update()).one()
                    db.rollback()
                    return "completed"
            except DBAPIError as exc:
                dbcode = getattr(getattr(exc.orig, "diag", None), "sqlstate", None)
                if dbcode == "40P01":
                    return "deadlock"
                raise

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                old = pool.submit(obligation_first)
                canonical = pool.submit(member_first)
                assert obligation_has_lock.wait(timeout=10)
                # Wait until both sides are actually queued; no sleep defines ordering.
                assert member_has_lock.wait(timeout=10)
                _await_both_waiting(engine, pids["obligation"], pids["member"])
                outcomes = {old.result(timeout=15), canonical.result(timeout=15)}
            assert "deadlock" in outcomes
        finally:
            _drop_probe(engine, probe)

    @pytest.mark.parametrize("writer_path", ["agreement-settlement", "agreement-reversal", "loan-pix-late-charge"])
    def test_unlocked_financial_writer_can_commit_while_member_lock_held(self, proof, writer_path):
        """A test-only obligation row can change after an old decision read."""
        engine, decisions, member_id, _contribution_id = proof
        probe = _probe_table(engine)
        writer_locked_installment = threading.Event()
        release_writer = threading.Event()
        writer_pid = []

        def current_pix_reservation_lock_path():
            with Session(engine, autoflush=False) as db:
                writer_pid.append(_pid(db))
                db.execute(sa.select(probe).where(probe.c.id == 1).with_for_update()).one()
                writer_locked_installment.set()
                assert release_writer.wait(timeout=10)
                db.execute(probe.update().where(probe.c.id == 1).values(value=1))
                db.commit()

        try:
            with Session(engine, autoflush=False) as worker:
                lock_member_financial_account(worker, member_id)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    writer = pool.submit(current_pix_reservation_lock_path)
                    try:
                        assert writer_locked_installment.wait(timeout=10)
                        # This path has no Member lock in the production inventory.
                        seen_value = worker.execute(
                            sa.select(probe.c.value).where(probe.c.id == 1)
                        ).scalar_one()
                        assert seen_value == 0
                    finally:
                        release_writer.set()
                    writer.result(timeout=15)
                worker.execute(decisions.insert().values(seen_paid=seen_value))
                worker.commit()
            with Session(engine) as observer:
                assert observer.execute(sa.select(probe.c.value).where(probe.c.id == 1)).scalar_one() == 1
                assert observer.execute(sa.select(decisions.c.seen_paid)).scalar_one() == 0
        finally:
            _drop_probe(engine, probe)

    def test_same_member_writers_serialize_but_different_members_do_not(self, proof):
        engine, _decisions, member_a, _contribution_id = proof
        member_b = _create_second_member(engine)
        first_holds = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        second_pid = []

        def hold_member(member_id, acquired=None, release=None):
            with Session(engine, autoflush=False) as db:
                lock_member_financial_account(db, member_id)
                if acquired is not None:
                    acquired.set()
                if release is not None:
                    assert release.wait(timeout=10)
                db.rollback()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(hold_member, member_a, first_holds, release_first)
            assert first_holds.wait(timeout=10)

            def second_same():
                with Session(engine, autoflush=False) as db:
                    second_pid.append(_pid(db))
                    second_started.set()
                    lock_member_financial_account(db, member_a)
                    db.rollback()

            second = pool.submit(second_same)
            try:
                assert second_started.wait(timeout=10)
                _await_waiting(engine, second_pid[0])
                assert not second.done()
            finally:
                release_first.set()
            first.result(timeout=15)
            second.result(timeout=15)

        # A separate Member row is not blocked by the first Member mutex.
        independent_locked = threading.Event()
        release_independent = threading.Event()
        with Session(engine, autoflush=False) as holder:
            lock_member_financial_account(holder, member_a)
            with ThreadPoolExecutor(max_workers=1) as pool:
                independent = pool.submit(
                    hold_member, member_b, independent_locked, release_independent,
                )
                assert independent_locked.wait(timeout=10)
                # Member B obtained its lock while Member A is still held.
                release_independent.set()
                independent.result(timeout=10)
            holder.rollback()
