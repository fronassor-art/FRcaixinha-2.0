from datetime import date
from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pytest

from app.db.base import Base
from app.models import Cycle, Member
from app.services.cycle_foundation import (
    CycleFoundationError,
    authoritative_amount,
    create_contribution,
    create_quota,
    ensure_contributions_for_entry,
    ensure_first_cycle,
)


def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def member(db):
    value = Member(user_id=db.query(Member).count() + 1, group_id=1, status="ACTIVE")
    db.add(value)
    db.flush()
    return value


def test_first_cycle_is_persisted_with_approved_terms():
    db = session()
    cycle = ensure_first_cycle(db)
    assert db.get(Cycle, cycle.id) is cycle
    assert (cycle.start_date, cycle.entry_deadline, cycle.closing_reference_date) == (
        date(2026, 12, 10), date(2027, 1, 10), date(2027, 12, 10)
    )
    assert cycle.monthly_amount == Decimal("150.00")
    assert cycle.months == 12
    assert cycle.max_quotas == 50


def test_member_can_hold_multiple_discrete_quotas_in_one_cycle():
    db = session()
    m = member(db)
    cycle = ensure_first_cycle(db)
    first = create_quota(db, member_id=m.id, cycle_id=cycle.id, units=1)
    second = create_quota(db, member_id=m.id, cycle_id=cycle.id, units=2)
    assert [first.units, second.units] == [1, 2]
    with pytest.raises(CycleFoundationError):
        create_quota(db, member_id=m.id, cycle_id=cycle.id, units=0)


def test_cycle_capacity_is_aggregate_and_authoritative_amount_is_decimal():
    db = session()
    cycle = ensure_first_cycle(db)
    for _ in range(50):
        m = member(db)
        create_quota(db, member_id=m.id, cycle_id=cycle.id, units=1)
    m = member(db)
    with pytest.raises(CycleFoundationError):
        create_quota(db, member_id=m.id, cycle_id=cycle.id, units=1)


def test_january_entry_creates_december_and_january_idempotently():
    db = session()
    m = member(db)
    cycle = ensure_first_cycle(db)
    create_quota(db, member_id=m.id, cycle_id=cycle.id, units=2)
    rows = ensure_contributions_for_entry(
        db, member_id=m.id, cycle_id=cycle.id, entry_date=date(2027, 1, 10)
    )
    again = ensure_contributions_for_entry(
        db, member_id=m.id, cycle_id=cycle.id, entry_date=date(2027, 1, 10)
    )
    assert [row.competence for row in rows] == [date(2026, 12, 1), date(2027, 1, 1)]
    assert rows[0].amount == rows[1].amount == Decimal("300.00")
    assert [row.id for row in again] == [row.id for row in rows]
    assert rows[0].status == rows[1].status == "PENDING"
    assert authoritative_amount(db, member_id=m.id, cycle_id=cycle.id) == Decimal("300.00")


def test_entry_after_deadline_is_rejected_without_financial_side_effects():
    db = session()
    m = member(db)
    cycle = ensure_first_cycle(db)
    create_quota(db, member_id=m.id, cycle_id=cycle.id, units=1)
    with pytest.raises(CycleFoundationError):
        create_contribution(
            db, member_id=m.id, cycle_id=cycle.id, competence=date(2027, 1, 1),
            entry_date=date(2027, 1, 11)
        )
    assert db.query(Cycle).count() == 1
