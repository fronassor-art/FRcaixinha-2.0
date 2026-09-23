"""A3.77B1 cycle participation and contribution charge rules."""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import (
    AuditLog, Contribution, ContributionChargeEvent, Cycle, CycleParticipation,
    Loan, LoanInstallment, Member, MemberFinancialEntry, Payment, PaymentSettlement,
)
from app.services.contribution_charge_v1 import (
    RULE_VERSION, calculate_contribution_charges,
)
from app.services.cycle_foundation import (
    CycleFoundationError, create_contribution, create_quota, ensure_first_cycle,
)
from app.services.cycle_participation import (
    CycleParticipationError, consecutive_overdue, ensure_active_participation,
    evaluate_delinquency, materialize_active_charges, voluntary_exit,
)

AMOUNT = Decimal("150.00")


def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def setup_member():
    db = session()
    member = Member(user_id=1, group_id=1, status="ACTIVE")
    db.add(member)
    db.flush()
    cycle = ensure_first_cycle(db)
    create_quota(db, member_id=member.id, cycle_id=cycle.id, units=1)
    participation = ensure_active_participation(db, member_id=member.id, cycle_id=cycle.id)
    return db, member, cycle, participation


def contribution(db, member, cycle, month, *, paid=Decimal("0.00"), year=2027,
                 paid_at=None):
    row = Contribution(
        member_id=member.id, cycle_id=cycle.id, competence=date(year, month, 1),
        amount=AMOUNT, due_date=date(year, month, 10), paid_amount=paid,
        status="PAID" if paid == AMOUNT else ("PARTIAL" if paid else "PENDING"),
        paid_at=paid_at,
    )
    db.add(row)
    db.flush()
    return row


def at(month, day=11):
    return datetime(2027, month, day, 12, tzinfo=timezone.utc)


def record_partial(db, row, *, amount=Decimal("50.00"), when=None):
    when = when or at(1, 12)
    payment = Payment(
        provider="test", provider_payment_id=f"test-{row.id}",
        idempotency_key=f"partial-{row.id}", amount=amount, status="approved",
        reference_type="CONTRIBUTION", reference_id=str(row.id),
    )
    db.add(payment)
    db.flush()
    settlement = PaymentSettlement(
        payment_id=payment.id, member_id=row.member_id, obligation_type="CONTRIBUTION",
        contribution_id=row.id, amount_received=amount, amount_applied=amount,
        principal_applied=amount, interest_applied=Decimal("0.00"),
        penalty_applied=Decimal("0.00"), excess_amount=Decimal("0.00"),
        obligation_status_before="OVERDUE", obligation_status_after="PARTIAL",
        confirmed_at=when, confirmation_source="TEST",
        receipt_number=f"TEST-{row.id}", receipt_version="v1",
        receipt_snapshot_json="{}", receipt_hash=f"test-{row.id}",
    )
    row.paid_amount = amount
    row.status = "PARTIAL"
    db.add(settlement)
    db.flush()


def test_unique_participation_and_member_in_next_cycle():
    db, member, cycle, first = setup_member()
    assert ensure_active_participation(db, member_id=member.id, cycle_id=cycle.id).id == first.id
    db.commit()
    db.add(Cycle(
        start_date=date(2028, 1, 10), entry_deadline=date(2028, 2, 10),
        closing_reference_date=date(2028, 12, 10), monthly_amount=AMOUNT,
        months=12, max_quotas=50, status="OPEN",
    ))
    db.flush()
    second_cycle = db.query(Cycle).filter(Cycle.start_date == date(2028, 1, 10)).one()
    create_quota(db, member_id=member.id, cycle_id=second_cycle.id, units=1)
    second = ensure_active_participation(db, member_id=member.id, cycle_id=second_cycle.id)
    assert second.id != first.id
    db.add(CycleParticipation(member_id=member.id, cycle_id=cycle.id, status="ACTIVE"))
    with pytest.raises(IntegrityError):
        db.flush()


@pytest.mark.parametrize("count, blocked", [(1, False), (2, False), (3, True)])
def test_only_three_consecutive_overdue_contributions_block(count, blocked):
    db, member, cycle, _ = setup_member()
    for month in range(1, count + 1):
        contribution(db, member, cycle, month)
    result = evaluate_delinquency(db, member_id=member.id, cycle_id=cycle.id, effective_at=at(4))
    assert (result.status == "BLOCKED_DELINQUENCY") is blocked
    assert member.status == "ACTIVE"


def test_nonconsecutive_overdue_does_not_block():
    db, member, cycle, _ = setup_member()
    for month in (1, 3, 5):
        contribution(db, member, cycle, month)
    result = evaluate_delinquency(db, member_id=member.id, cycle_id=cycle.id, effective_at=at(6))
    assert result.status == "ACTIVE"


def test_partial_counts_and_full_payment_breaks_sequence():
    db, member, cycle, _ = setup_member()
    rows = [contribution(db, member, cycle, m) for m in (1, 2, 3, 4)]
    rows[1].paid_amount = Decimal("50.00")
    rows[1].status = "PARTIAL"
    assert consecutive_overdue(rows[:3], date(2027, 4, 11))
    rows[1].paid_amount = AMOUNT
    rows[1].status = "PAID"
    assert not consecutive_overdue(rows, date(2027, 5, 11))


def test_charge_rule_boundaries_partial_base_and_no_compounding():
    due = date(2027, 1, 10)
    def charge(day, payments=()):
        return calculate_contribution_charges(
            principal=AMOUNT, due_date=due, through=day,
            principal_payments=payments,
        )
    assert charge(date(2027, 1, 9)).fixed_penalty == Decimal("0.00")
    assert charge(due).daily_interest == Decimal("0.00")
    assert charge(date(2027, 1, 11)).fixed_penalty == Decimal("10.00")
    assert charge(date(2027, 1, 11)).daily_interest == Decimal("1.00")
    partial = charge(date(2027, 1, 12), ((date(2027, 1, 12), Decimal("50.00")),))
    assert partial.fixed_penalty == Decimal("10.00")
    assert partial.daily_interest == Decimal("1.67")
    assert charge(date(2027, 2, 9)).daily_interest == Decimal("30.00")
    assert charge(date(2027, 2, 9)).fixed_penalty == Decimal("10.00")


def test_belem_utc_boundary_and_decimal_only():
    due = date(2027, 1, 10)
    before = calculate_contribution_charges(
        principal=AMOUNT, due_date=due,
        through=datetime(2027, 1, 11, 2, 59, tzinfo=timezone.utc),
    )
    after = calculate_contribution_charges(
        principal=AMOUNT, due_date=due,
        through=datetime(2027, 1, 11, 3, 0, tzinfo=timezone.utc),
    )
    assert before.fixed_penalty == Decimal("0.00")
    assert after.fixed_penalty == Decimal("10.00")
    assert after.daily_interest == Decimal("1.00")
    with pytest.raises(TypeError):
        calculate_contribution_charges(principal=150.0, due_date=due, through=due)


def test_accrual_snapshots_are_versioned_and_idempotent():
    db, member, cycle, _ = setup_member()
    row = contribution(db, member, cycle, 1)
    assert materialize_active_charges(db, member_id=member.id, cycle_id=cycle.id, effective_at=at(1)) == 1
    assert materialize_active_charges(db, member_id=member.id, cycle_id=cycle.id, effective_at=at(1)) == 0
    assert materialize_active_charges(db, member_id=member.id, cycle_id=cycle.id, effective_at=at(1, 12)) == 1
    events = db.query(ContributionChargeEvent).filter_by(contribution_id=row.id).all()
    assert len(events) == 2
    assert [e.daily_interest for e in events] == [Decimal("1.00"), Decimal("2.00")]
    assert all(e.rule_version == RULE_VERSION for e in events)


def test_block_freezes_charges_cancels_principal_and_is_idempotent():
    db, member, cycle, participation = setup_member()
    rows = [contribution(db, member, cycle, m) for m in (1, 2, 3, 5, 6)]
    loan = Loan(member_id=member.id, principal=Decimal("100.00"),
                monthly_rate=Decimal("0.20"), installments=1, status="ACTIVE")
    db.add(loan)
    db.flush()
    first = evaluate_delinquency(db, member_id=member.id, cycle_id=cycle.id, effective_at=at(4))
    before = (first.blocked_at, db.query(ContributionChargeEvent).count(),
              db.query(AuditLog).count())
    second = evaluate_delinquency(db, member_id=member.id, cycle_id=cycle.id, effective_at=at(6))
    assert second.id == first.id == participation.id
    assert before == (second.blocked_at, db.query(ContributionChargeEvent).count(),
                      db.query(AuditLog).count())
    assert before[1] == 5
    assert all(c.status == "CANCELLED" and c.cancelled_at == first.blocked_at for c in rows)
    assert sum((e.cancelled_principal for e in db.query(ContributionChargeEvent)), Decimal("0")) == AMOUNT * 5
    assert db.query(MemberFinancialEntry).count() == 0
    assert db.get(Loan, loan.id).status == "ACTIVE"
    assert member.status == "ACTIVE"
    with pytest.raises(CycleParticipationError):
        ensure_active_participation(db, member_id=member.id, cycle_id=cycle.id)
    with pytest.raises(CycleParticipationError):
        create_contribution(db, member_id=member.id, cycle_id=cycle.id,
                            competence=date(2027, 7, 1), entry_date=date(2027, 7, 1))
    rows[0].paid_amount = AMOUNT
    assert evaluate_delinquency(db, member_id=member.id, cycle_id=cycle.id,
                                effective_at=at(7)).status == "BLOCKED_DELINQUENCY"
    next_cycle = Cycle(
        start_date=date(2028, 1, 10), entry_deadline=date(2028, 2, 10),
        closing_reference_date=date(2028, 12, 10), monthly_amount=AMOUNT,
        months=12, max_quotas=50, status="OPEN",
    )
    db.add(next_cycle)
    db.flush()
    create_quota(db, member_id=member.id, cycle_id=next_cycle.id, units=1)
    assert ensure_active_participation(
        db, member_id=member.id, cycle_id=next_cycle.id
    ).status == "ACTIVE"


def test_partial_payment_reduces_future_mora_at_block():
    db, member, cycle, _ = setup_member()
    rows = [contribution(db, member, cycle, m) for m in (1, 2, 3)]
    record_partial(db, rows[0])
    result = evaluate_delinquency(db, member_id=member.id, cycle_id=cycle.id,
                                  effective_at=at(4))
    assert result.status == "BLOCKED_DELINQUENCY"
    event = db.query(ContributionChargeEvent).filter_by(
        contribution_id=rows[0].id, event_type="BLOCK_FREEZE"
    ).one()
    assert event.cancelled_principal == Decimal("100.00")
    assert event.fixed_penalty == Decimal("10.00")
    assert event.daily_interest == Decimal("61.00")


def test_voluntary_exit_requires_current_obligations_and_keeps_paid_history():
    db, member, cycle, participation = setup_member()
    paid = contribution(
        db, member, cycle, 12, year=2026, paid=AMOUNT,
        paid_at=datetime(2026, 12, 9, 12, tzinfo=timezone.utc),
    )
    future = contribution(db, member, cycle, 1)
    loan = Loan(
        member_id=member.id, principal=Decimal("100.00"),
        monthly_rate=Decimal("0.20"), installments=1, status="REQUESTED",
    )
    db.add(loan)
    db.flush()
    row = voluntary_exit(db, member_id=member.id, cycle_id=cycle.id,
                         effective_at=datetime(2027, 1, 5, 12, tzinfo=timezone.utc))
    assert row.id == participation.id
    assert row.status == "VOLUNTARILY_EXITED"
    assert paid.status == "PAID"
    assert future.status == "CANCELLED"
    assert future.cancelled_at == row.voluntary_exit_at
    assert db.query(ContributionChargeEvent).count() == 0
    assert member.status == "ACTIVE"
    assert db.get(Loan, loan.id).status == "REQUESTED"
    with pytest.raises(CycleParticipationError):
        create_contribution(
            db, member_id=member.id, cycle_id=cycle.id,
            competence=date(2027, 2, 1), entry_date=date(2027, 2, 1),
        )
    with pytest.raises(CycleParticipationError):
        voluntary_exit(db, member_id=member.id, cycle_id=cycle.id,
                       effective_at=at(1))


def test_voluntary_exit_rejects_overdue_contribution():
    db, member, cycle, _ = setup_member()
    contribution(db, member, cycle, 1)
    with pytest.raises(CycleParticipationError):
        voluntary_exit(db, member_id=member.id, cycle_id=cycle.id,
                       effective_at=at(2))
    assert db.query(CycleParticipation).one().status == "ACTIVE"



def test_voluntary_exit_rejects_overdue_loan_without_mutating_it():
    db, member, cycle, _ = setup_member()
    loan = Loan(
        member_id=member.id, principal=Decimal("100.00"),
        monthly_rate=Decimal("0.20"), installments=1, status="ACTIVE",
    )
    db.add(loan)
    db.flush()
    installment = LoanInstallment(
        loan_id=loan.id, number=1, due_date=date(2026, 12, 20),
        principal=Decimal("100.00"), interest=Decimal("20.00"),
        amount=Decimal("120.00"), paid_amount=Decimal("0.00"),
        penalty_amount=Decimal("0.00"), paid_penalty_amount=Decimal("0.00"),
        status="OPEN",
    )
    db.add(installment)
    db.flush()
    with pytest.raises(CycleParticipationError, match="overdue loan"):
        voluntary_exit(
            db, member_id=member.id, cycle_id=cycle.id,
            effective_at=datetime(2027, 1, 5, 12, tzinfo=timezone.utc),
        )
    assert loan.status == "ACTIVE"
    assert installment.status == "OPEN"


def test_entry_date_is_stored_in_belem_utc():
    db = session()
    member = Member(user_id=21, group_id=1, status="ACTIVE")
    db.add(member)
    db.flush()
    cycle = ensure_first_cycle(db)
    create_quota(db, member_id=member.id, cycle_id=cycle.id, units=1)
    create_contribution(
        db, member_id=member.id, cycle_id=cycle.id,
        competence=date(2027, 1, 1), entry_date=date(2027, 1, 10),
    )
    row = db.query(CycleParticipation).filter_by(member_id=member.id).one()
    assert row.joined_at.replace(tzinfo=timezone.utc) == datetime(2027, 1, 10, 3, 0, tzinfo=timezone.utc)
