"""Dedicated PostgreSQL integration proof for the A3.77A quota lock."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.models import Cycle, Group, Member, Quota, User
from app.services.cycle_foundation import (
    CycleFoundationError,
    FIRST_CYCLE_START,
    create_quota,
)


ENV_NAME = "A377A_POSTGRES_TEST_URL"


def test_postgres_quota_capacity_is_serialized_under_concurrency():
    raw_url = os.environ.get(ENV_NAME)
    if not raw_url:
        pytest.skip(f"{ENV_NAME} is not configured; dedicated PostgreSQL test not run")

    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql":
        pytest.fail(f"{ENV_NAME} must use PostgreSQL; refusing other dialects")
    database = url.database or ""
    if "a377a_test" not in database.lower():
        pytest.fail(
            f"{ENV_NAME} database name must contain 'a377a_test'; refusing non-dedicated database"
        )

    engine = create_engine(raw_url, pool_pre_ping=True)
    assert engine.dialect.name == "postgresql", "refusing to run against a non-PostgreSQL engine"
    SessionLocal = __import__("sqlalchemy.orm", fromlist=["sessionmaker"]).sessionmaker(
        bind=engine, expire_on_commit=False
    )

    tag = uuid4().hex
    group_id = member_id = cycle_id = user_id = None
    try:
        with SessionLocal() as setup:
            group = Group(name=f"a377a-test-{tag}")
            setup.add(group)
            setup.flush()
            user = User(
                name=f"A377A {tag}", email=f"a377a-{tag}@example.invalid",
                cpf=f"{int(tag[:11], 16) % 10**11:011d}", password_hash="test-only",
            )
            setup.add(user)
            setup.flush()
            member = Member(user_id=user.id, group_id=group.id, status="ACTIVE")
            setup.add(member)
            setup.flush()

            cycle = (
                setup.query(Cycle)
                .filter(Cycle.start_date == FIRST_CYCLE_START)
                .one()
            )
            assert cycle.max_quotas == 50

            user_id, group_id, member_id, cycle_id = (
                user.id,
                group.id,
                member.id,
                cycle.id,
            )
            for _ in range(49):
                create_quota(setup, member_id=member_id, cycle_id=cycle_id, units=1)
            setup.commit()

        with SessionLocal() as check:
            initial = check.query(func.coalesce(func.sum(Quota.units), 0)).filter(
                Quota.cycle_id == cycle_id
            ).scalar()
            assert int(initial or 0) == 49

        barrier = Barrier(2)

        def attempt():
            with SessionLocal() as worker:
                barrier.wait(timeout=15)
                try:
                    create_quota(worker, member_id=member_id, cycle_id=cycle_id, units=1)
                    worker.commit()
                    return ("success", None)
                except CycleFoundationError as exc:
                    worker.rollback()
                    return ("rejected", str(exc))

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: attempt(), range(2)))

        assert sum(kind == "success" for kind, _ in outcomes) == 1, outcomes
        rejected = [message for kind, message in outcomes if kind == "rejected"]
        assert rejected == ["cycle quota capacity exceeded"], outcomes

        with SessionLocal() as final_check:
            total = final_check.query(func.coalesce(func.sum(Quota.units), 0)).filter(
                Quota.cycle_id == cycle_id
            ).scalar()
            assert int(total or 0) == 50
            assert int(total or 0) <= 50
    finally:
        with SessionLocal() as cleanup:
            if member_id is not None:
                cleanup.query(Quota).filter(
                    Quota.member_id == member_id,
                    Quota.cycle_id == cycle_id,
                ).delete(synchronize_session=False)
                cleanup.query(Member).filter(
                    Member.id == member_id
                ).delete(synchronize_session=False)

            if user_id is not None:
                cleanup.query(User).filter(
                    User.id == user_id
                ).delete(synchronize_session=False)

            if group_id is not None:
                cleanup.query(Group).filter(
                    Group.id == group_id
                ).delete(synchronize_session=False)

            cleanup.commit()

        engine.dispose()
