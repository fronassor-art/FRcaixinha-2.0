"""PostgreSQL row lock proof for duplicate block attempts."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.models import (
    AuditLog, Contribution, ContributionChargeEvent, Cycle,
    CycleParticipation, Group, Member, MemberFinancialAccount, Quota, User,
)
from app.services.cycle_participation import ensure_active_participation, evaluate_delinquency


def test_postgres_concurrent_block_has_one_transition_and_one_freeze_per_contribution():
    raw_url = os.environ.get("A377B1_POSTGRES_TEST_URL")
    if not raw_url:
        pytest.skip("A377B1_POSTGRES_TEST_URL is not configured")
    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql" or "a377b1_test" not in (url.database or "").lower():
        pytest.fail("URL must target the dedicated a377b1_test PostgreSQL database")
    engine = create_engine(raw_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    tag = uuid4().hex
    group_id = user_id = member_id = participation_id = None
    try:
        with SessionLocal() as db:
            group = Group(name="a377b1-" + tag)
            user = User(
                name="A377B1 " + tag, email=tag + "@example.invalid",
                cpf=f"{int(tag[:11], 16) % 10**11:011d}", password_hash="test",
            )
            db.add_all([group, user])
            db.flush()
            member = Member(user_id=user.id, group_id=group.id, status="ACTIVE")
            db.add(member)
            db.flush()
            cycle = db.query(Cycle).filter(Cycle.start_date == date(2026, 12, 10)).one()
            db.add(Quota(member_id=member.id, cycle_id=cycle.id, units=Decimal("1"), status="ACTIVE"))
            db.flush()
            participation = ensure_active_participation(
                db, member_id=member.id, cycle_id=cycle.id
            )
            for month in (1, 2, 3, 5):
                db.add(Contribution(
                    member_id=member.id, cycle_id=cycle.id,
                    competence=date(2027, month, 1), due_date=date(2027, month, 10),
                    amount=Decimal("150.00"), paid_amount=Decimal("0.00"),
                    status="PENDING",
                ))
            db.commit()
            group_id, user_id, member_id, participation_id = (
                group.id, user.id, member.id, participation.id
            )
            cycle_id = cycle.id
        barrier = Barrier(2)
        when = datetime(2027, 4, 11, 12, tzinfo=timezone.utc)
        def attempt(_):
            with SessionLocal() as db:
                barrier.wait(timeout=15)
                row = evaluate_delinquency(
                    db, member_id=member_id, cycle_id=cycle_id, effective_at=when
                )
                db.commit()
                return row.status
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(attempt, range(2)))
        assert outcomes == ["BLOCKED_DELINQUENCY", "BLOCKED_DELINQUENCY"]
        with SessionLocal() as db:
            row = db.get(CycleParticipation, participation_id)
            assert row.status == "BLOCKED_DELINQUENCY"
            assert row.blocked_at == when
            assert db.query(ContributionChargeEvent).filter_by(
                participation_id=participation_id
            ).count() == 4
            assert db.query(AuditLog).filter_by(
                action="CYCLE_PARTICIPATION_BLOCKED",
                entity_type="CYCLE_PARTICIPATION",
                entity_id=str(participation_id),
            ).count() == 1
    finally:
        with SessionLocal() as db:
            if participation_id is not None:
                db.query(AuditLog).filter_by(
                    entity_type="CYCLE_PARTICIPATION",
                    entity_id=str(participation_id),
                ).delete(synchronize_session=False)
                db.query(ContributionChargeEvent).filter_by(
                    participation_id=participation_id
                ).delete(synchronize_session=False)
                db.query(Contribution).filter_by(
                    member_id=member_id
                ).delete(synchronize_session=False)
                db.query(CycleParticipation).filter_by(
                    id=participation_id
                ).delete(synchronize_session=False)
                db.query(Quota).filter_by(member_id=member_id).delete(synchronize_session=False)
                db.query(MemberFinancialAccount).filter_by(
                    member_id=member_id
                ).delete(synchronize_session=False)
                db.query(Member).filter_by(id=member_id).delete(synchronize_session=False)
            if user_id is not None:
                db.query(User).filter_by(id=user_id).delete(synchronize_session=False)
            if group_id is not None:
                db.query(Group).filter_by(id=group_id).delete(synchronize_session=False)
            db.commit()
        engine.dispose()
