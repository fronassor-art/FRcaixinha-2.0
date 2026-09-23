from __future__ import annotations
from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.models import Contribution, Cycle, CycleParticipation, Member, Quota
from app.services.cycle_participation import ensure_active_participation

FIRST_CYCLE_START = date(2026, 12, 10)
FIRST_CYCLE_ENTRY_DEADLINE = date(2027, 1, 10)
FIRST_CYCLE_CLOSING_REFERENCE = date(2027, 12, 10)
FIRST_CYCLE_MONTHLY_AMOUNT = Decimal("150.00")
FIRST_CYCLE_MONTHS = 12
FIRST_CYCLE_MAX_QUOTAS = 50
MONEY = Decimal("0.01")

class CycleFoundationError(ValueError):
    pass

def money(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError("financial inputs must be Decimal")
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)

def ensure_first_cycle(db: Session) -> Cycle:
    cycle = db.query(Cycle).filter(Cycle.start_date == FIRST_CYCLE_START).one_or_none()
    if cycle:
        return cycle
    cycle = Cycle(start_date=FIRST_CYCLE_START, entry_deadline=FIRST_CYCLE_ENTRY_DEADLINE,
        closing_reference_date=FIRST_CYCLE_CLOSING_REFERENCE, monthly_amount=FIRST_CYCLE_MONTHLY_AMOUNT,
        months=FIRST_CYCLE_MONTHS, max_quotas=FIRST_CYCLE_MAX_QUOTAS, status="OPEN")
    db.add(cycle)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        cycle = db.query(Cycle).filter(Cycle.start_date == FIRST_CYCLE_START).one()
    return cycle

def _cycle_for_update(db: Session, cycle_id: int) -> Cycle:
    cycle = db.query(Cycle).filter(Cycle.id == cycle_id).with_for_update().one_or_none()
    if cycle is None:
        raise CycleFoundationError("cycle not found")
    return cycle

def create_quota(db: Session, *, member_id: int, cycle_id: int, units: int) -> Quota:
    if not isinstance(units, int) or isinstance(units, bool) or units < 1:
        raise CycleFoundationError("quota units must be a positive integer")
    cycle = _cycle_for_update(db, cycle_id)
    member_query = db.query(Member).filter(Member.id == member_id)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        member_query = member_query.with_for_update().populate_existing()
    if member_query.one_or_none() is None:
        raise CycleFoundationError("member not found")
    existing_query = db.query(CycleParticipation).filter(
        CycleParticipation.member_id == member_id, CycleParticipation.cycle_id == cycle_id
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        existing_query = existing_query.with_for_update().populate_existing()
    existing_participation = existing_query.one_or_none()
    if existing_participation is not None and existing_participation.status != "ACTIVE":
        raise CycleFoundationError("participation is closed for this cycle")
    total = db.query(func.coalesce(func.sum(Quota.units), 0)).filter(Quota.cycle_id == cycle.id).scalar()
    if int(total or 0) + units > cycle.max_quotas:
        raise CycleFoundationError("cycle quota capacity exceeded")
    quota = Quota(member_id=member_id, cycle_id=cycle.id, units=units, status="ACTIVE")
    db.add(quota)
    db.flush()
    return quota

def _quota_units(db: Session, member_id: int, cycle_id: int) -> int:
    total = db.query(func.coalesce(func.sum(Quota.units), 0)).filter(
        Quota.member_id == member_id, Quota.cycle_id == cycle_id, Quota.status == "ACTIVE").scalar()
    if not total:
        raise CycleFoundationError("member has no active quota in cycle")
    return int(total)

def authoritative_amount(db: Session, *, member_id: int, cycle_id: int) -> Decimal:
    cycle = db.get(Cycle, cycle_id)
    if cycle is None:
        raise CycleFoundationError("cycle not found")
    return money(Decimal(cycle.monthly_amount) * _quota_units(db, member_id, cycle_id))

def create_contribution(db: Session, *, member_id: int, cycle_id: int,
    competence: date, entry_date: date) -> Contribution:
    cycle = db.get(Cycle, cycle_id)
    if cycle is None:
        raise CycleFoundationError("cycle not found")
    participation = db.query(CycleParticipation).filter(
        CycleParticipation.member_id == member_id, CycleParticipation.cycle_id == cycle_id
    ).one_or_none()
    if participation is None and entry_date > cycle.entry_deadline:
        raise CycleFoundationError("cycle entry deadline exceeded")
    if entry_date < cycle.start_date:
        raise CycleFoundationError("entry precedes cycle")
    if competence.day != 1:
        raise CycleFoundationError("competence must be the first day of a month")
    if competence < cycle.start_date.replace(day=1):
        raise CycleFoundationError("competence precedes cycle")
    start_month = cycle.start_date.year * 12 + cycle.start_date.month
    competence_month = competence.year * 12 + competence.month
    if competence_month >= start_month + cycle.months:
        raise CycleFoundationError("competence exceeds cycle")
    ensure_active_participation(db, member_id=member_id, cycle_id=cycle_id, entry_date=entry_date)
    existing = db.query(Contribution).filter(
        Contribution.member_id == member_id, Contribution.cycle_id == cycle_id,
        Contribution.competence == competence).one_or_none()
    if existing:
        expected = authoritative_amount(db, member_id=member_id, cycle_id=cycle_id)
        if money(Decimal(existing.amount)) != expected:
            raise CycleFoundationError("existing contribution amount is not authoritative")
        return existing
    amount = authoritative_amount(db, member_id=member_id, cycle_id=cycle_id)
    contribution = Contribution(member_id=member_id, cycle_id=cycle_id, competence=competence,
        amount=amount, status="PENDING",
        due_date=date(competence.year, competence.month, min(10, monthrange(competence.year, competence.month)[1])),
        paid_amount=Decimal("0.00"))
    db.add(contribution)
    db.flush()
    return contribution

def ensure_contributions_for_entry(db: Session, *, member_id: int, cycle_id: int,
    entry_date: date) -> list[Contribution]:
    cycle = db.get(Cycle, cycle_id)
    if cycle is None:
        raise CycleFoundationError("cycle not found")
    participation = db.query(CycleParticipation).filter(
        CycleParticipation.member_id == member_id, CycleParticipation.cycle_id == cycle_id
    ).one_or_none()
    if participation is None and entry_date > cycle.entry_deadline:
        raise CycleFoundationError("cycle entry deadline exceeded")
    start = cycle.start_date.replace(day=1)
    target = entry_date.replace(day=1)
    if entry_date < cycle.start_date:
        raise CycleFoundationError("entry precedes cycle")
    ensure_active_participation(db, member_id=member_id, cycle_id=cycle_id, entry_date=entry_date)
    months = min(cycle.months, (target.year - start.year) * 12 + target.month - start.month + 1)
    result = []
    for offset in range(months):
        month_index = start.month - 1 + offset
        year = start.year + month_index // 12
        month = month_index % 12 + 1
        result.append(create_contribution(db, member_id=member_id, cycle_id=cycle_id,
            competence=date(year, month, 1), entry_date=entry_date))
    return result
