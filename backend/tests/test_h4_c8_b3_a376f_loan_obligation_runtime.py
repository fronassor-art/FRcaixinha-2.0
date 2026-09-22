from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.loan_rules import (
    LATE_CHARGE_SETTLEMENT_COMPONENT_VERSION,
    LATE_CHARGE_VERSION,
)
from app.db.base import Base
from app.models import (
    Group,
    Loan,
    LoanInstallment,
    LoanLateChargeEvent,
    Member,
    Payment,
    PaymentReversal,
    PaymentSettlement,
    User,
)
from app.services.loan_obligation_runtime import (
    FIXED_PENALTY_AFTER_TIMELY_PAYMENT_REVERSAL,
    FIXED_PENALTY_EFFECTIVE_DATE_SEMANTICS,
    late_interest_materialized,
    loan_installment_obligation,
    materialize_daily_late_interest,
    materialize_fixed_penalty,
    materialize_reversal_late_interest_adjustment,
    materialize_settlement_late_interest_adjustment,
    reconstruct_late_principal_base,
)


UTC = timezone.utc


def _db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _installment(db, *, due=date(2026, 1, 1), principal="100.00", interest="20.00"):
    suffix = str(id(db))
    group = Group(name=f"Runtime {suffix}", max_installments=6)
    user = User(
        name="Runtime member",
        email=f"runtime-{suffix}@test",
        cpf=f"runtime-{suffix}",
        password_hash="x",
    )
    db.add_all([group, user])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id)
    db.add(member)
    db.flush()
    loan = Loan(
        member_id=member.id,
        principal=Decimal(principal),
        monthly_rate=Decimal("0.20"),
        installments=1,
        calculation_version="price_amortization_v1",
        status="ACTIVE",
    )
    db.add(loan)
    db.flush()
    installment = LoanInstallment(
        loan_id=loan.id,
        number=1,
        due_date=due,
        principal=Decimal(principal),
        interest=Decimal(interest),
        amount=Decimal(principal) + Decimal(interest),
        paid_amount=Decimal("0.00"),
        penalty_amount=Decimal("0.00"),
        paid_penalty_amount=Decimal("0.00"),
        status="OPEN",
    )
    db.add(installment)
    db.flush()
    return member, user, installment


def _settlement(
    db,
    member,
    installment,
    *,
    suffix,
    confirmed_at,
    principal="0.00",
    normal="0.00",
    late="0.00",
    fixed="0.00",
    versioned=True,
):
    principal = Decimal(principal)
    normal = Decimal(normal)
    late = Decimal(late)
    fixed = Decimal(fixed)
    applied = principal + normal + late + fixed
    payment = Payment(
        provider="runtime-test",
        provider_payment_id=f"provider-{suffix}",
        idempotency_key=f"key-{suffix}",
        amount=applied,
        amount_received=applied,
        status="approved",
        reference_type="LOAN_INSTALLMENT",
        reference_id=str(installment.id),
        confirmed_at=confirmed_at,
    )
    db.add(payment)
    db.flush()
    settlement = PaymentSettlement(
        payment_id=payment.id,
        member_id=member.id,
        obligation_type="LOAN_INSTALLMENT",
        loan_installment_id=installment.id,
        amount_received=applied,
        amount_applied=applied,
        principal_applied=principal,
        interest_applied=normal,
        penalty_applied=late + fixed,
        settlement_component_version=(
            LATE_CHARGE_SETTLEMENT_COMPONENT_VERSION if versioned else None
        ),
        normal_interest_applied=normal if versioned else None,
        late_interest_applied=late if versioned else None,
        fixed_penalty_applied=fixed if versioned else None,
        excess_amount=Decimal("0.00"),
        obligation_status_before="OPEN",
        obligation_status_after="PARTIAL",
        confirmed_at=confirmed_at,
        confirmation_source="RUNTIME_TEST",
        receipt_number=f"RUNTIME-{suffix}",
        receipt_version="v1",
        receipt_snapshot_json="{}",
        receipt_hash=f"hash-{suffix}",
    )
    db.add(settlement)
    db.flush()
    return settlement


def _reversal(db, user, installment, settlement, *, suffix, reversed_at):
    reversal = PaymentReversal(
        payment_id=settlement.payment_id,
        settlement_id=settlement.id,
        admin_id=user.id,
        reason="Reversão runtime válida",
        reversed_at=reversed_at,
        reversal_competence=date(reversed_at.year, reversed_at.month, 1),
        original_due_date=installment.due_date,
        original_date_kind="LOAN_INSTALLMENT_DUE_DATE",
        amount_received=settlement.amount_received,
        amount_applied=settlement.amount_applied,
        principal_applied=settlement.principal_applied,
        interest_applied=settlement.interest_applied,
        penalty_applied=settlement.penalty_applied,
        excess_amount=settlement.excess_amount,
        receipt_number=f"REV-{suffix}",
        receipt_version="v1",
        receipt_snapshot_json="{}",
        receipt_hash=f"rev-hash-{suffix}",
    )
    db.add(reversal)
    db.flush()
    return reversal


def test_gate_none_legacy_before_gate_and_not_due_remain_inactive():
    db = _db()
    _, _, installment = _installment(db)

    assert FIXED_PENALTY_EFFECTIVE_DATE_SEMANTICS == "DUE_DATE_PLUS_ONE_CIVIL_DAY"
    assert materialize_daily_late_interest(
        db,
        installment.id,
        through_date=date(2026, 1, 5),
        late_charge_effective_date=None,
    ) == []
    assert materialize_daily_late_interest(
        db,
        installment.id,
        through_date=date(2026, 1, 5),
        late_charge_effective_date=date(2026, 1, 2),
    ) == []
    assert materialize_daily_late_interest(
        db,
        installment.id,
        through_date=installment.due_date,
        late_charge_effective_date=installment.due_date,
    ) == []
    assert installment.late_charge_version is None
    assert db.query(LoanLateChargeEvent).count() == 0
    db.close()


def test_daily_accrual_at_gate_is_cumulative_idempotent_and_non_compounding():
    db = _db()
    _, _, installment = _installment(db)

    first = materialize_daily_late_interest(
        db,
        installment.id,
        through_date=date(2026, 1, 2),
        late_charge_effective_date=installment.due_date,
    )
    assert [row.amount for row in first] == [Decimal("0.67")]

    catch_up = materialize_daily_late_interest(
        db,
        installment.id,
        through_date=date(2026, 1, 4),
        late_charge_effective_date=installment.due_date,
    )
    assert [row.amount for row in catch_up] == [Decimal("0.66"), Decimal("0.67")]
    assert late_interest_materialized(db, installment.id) == Decimal("2.00")
    assert installment.late_interest_amount == Decimal("2.00")
    assert installment.fixed_penalty_amount == Decimal("0.00")
    assert installment.late_interest_accrued_through_date == date(2026, 1, 4)

    assert materialize_daily_late_interest(
        db,
        installment.id,
        through_date=date(2026, 1, 4),
        late_charge_effective_date=installment.due_date,
    ) == []
    assert materialize_daily_late_interest(
        db,
        installment.id,
        through_date=date(2026, 1, 3),
        late_charge_effective_date=installment.due_date,
    ) == []
    assert installment.late_interest_accrued_through_date == date(2026, 1, 4)
    assert db.query(LoanLateChargeEvent).count() == 3
    db.close()


def test_partial_settlement_reduces_base_on_p_plus_one_and_adjusts_once():
    db = _db()
    member, _, installment = _installment(db)
    materialize_daily_late_interest(
        db,
        installment.id,
        through_date=date(2026, 1, 5),
        late_charge_effective_date=installment.due_date,
    )
    assert late_interest_materialized(db, installment.id) == Decimal("2.67")

    settlement = _settlement(
        db,
        member,
        installment,
        suffix="partial",
        confirmed_at=datetime(2026, 1, 3, 12, tzinfo=UTC),
        principal="40.00",
    )
    assert reconstruct_late_principal_base(db, installment, date(2026, 1, 3)) == Decimal("100.00")
    assert reconstruct_late_principal_base(db, installment, date(2026, 1, 4)) == Decimal("60.00")

    event = materialize_settlement_late_interest_adjustment(db, settlement.id)
    repeated = materialize_settlement_late_interest_adjustment(db, settlement.id)
    assert event.id == repeated.id
    assert event.event_type == "LATE_INTEREST_ADJUSTMENT_DECREASE"
    assert event.effective_date == date(2026, 1, 4)
    assert event.amount == Decimal("0.54")
    assert event.eligible_principal is None
    assert late_interest_materialized(db, installment.id) == Decimal("2.13")
    assert installment.late_interest_accrued_through_date == date(2026, 1, 5)
    materialize_daily_late_interest(
        db,
        installment.id,
        through_date=date(2026, 1, 4),
        late_charge_effective_date=installment.due_date,
    )
    assert installment.late_interest_accrued_through_date == date(2026, 1, 5)
    db.close()


def test_full_settlement_and_reversal_restore_base_on_the_next_financial_day():
    db = _db()
    member, user, installment = _installment(db)
    materialize_daily_late_interest(
        db,
        installment.id,
        through_date=date(2026, 1, 5),
        late_charge_effective_date=installment.due_date,
    )
    settlement = _settlement(
        db,
        member,
        installment,
        suffix="full",
        confirmed_at=datetime(2026, 1, 2, 12, tzinfo=UTC),
        principal="100.00",
    )
    decrease = materialize_settlement_late_interest_adjustment(db, settlement.id)
    assert decrease.amount == Decimal("2.00")
    assert late_interest_materialized(db, installment.id) == Decimal("0.67")

    reversal = _reversal(
        db,
        user,
        installment,
        settlement,
        suffix="full",
        reversed_at=datetime(2026, 1, 4, 12, tzinfo=UTC),
    )
    assert reconstruct_late_principal_base(db, installment, date(2026, 1, 4)) == ZERO
    assert reconstruct_late_principal_base(db, installment, date(2026, 1, 5)) == Decimal("100.00")
    increase = materialize_reversal_late_interest_adjustment(db, reversal.id)
    repeated = materialize_reversal_late_interest_adjustment(db, reversal.id)
    assert increase.id == repeated.id
    assert increase.event_type == "LATE_INTEREST_ADJUSTMENT_INCREASE"
    assert increase.effective_date == date(2026, 1, 5)
    assert increase.amount == Decimal("0.66")
    assert late_interest_materialized(db, installment.id) == Decimal("1.33")
    db.close()


def test_belem_midnight_boundary_controls_principal_base():
    db = _db()
    member, _, installment = _installment(db)
    _settlement(
        db,
        member,
        installment,
        suffix="belem",
        # 02:30 UTC is still January 3 in America/Belem.
        confirmed_at=datetime(2026, 1, 4, 2, 30, tzinfo=UTC),
        principal="25.00",
    )
    assert reconstruct_late_principal_base(db, installment, date(2026, 1, 3)) == Decimal("100.00")
    assert reconstruct_late_principal_base(db, installment, date(2026, 1, 4)) == Decimal("75.00")
    db.close()


def test_authoritative_read_model_has_four_components_model_b_and_valid_payments():
    db = _db()
    member, _, installment = _installment(db, due=date(2026, 2, 1))
    assert loan_installment_obligation(
        db, installment, date(2026, 1, 31)
    ).total_due == ZERO

    installment.late_charge_version = LATE_CHARGE_VERSION
    installment.fixed_penalty_amount = Decimal("10.00")
    installment.paid_fixed_penalty_amount = ZERO
    installment.late_interest_amount = Decimal("3.00")
    installment.paid_late_interest_amount = ZERO
    db.add(
        LoanLateChargeEvent(
            loan_installment_id=installment.id,
            late_charge_version=LATE_CHARGE_VERSION,
            event_type="FIXED_PENALTY_ASSESSED",
            effective_date=date(2026, 2, 2),
            amount=Decimal("10.00"),
        )
    )
    db.add(
        LoanLateChargeEvent(
            loan_installment_id=installment.id,
            late_charge_version=LATE_CHARGE_VERSION,
            event_type="LATE_INTEREST_ACCRUED",
            effective_date=date(2026, 2, 2),
            amount=Decimal("3.00"),
            eligible_principal=Decimal("100.00"),
        )
    )
    db.flush()
    _settlement(
        db,
        member,
        installment,
        suffix="components",
        confirmed_at=datetime(2026, 2, 2, 12, tzinfo=UTC),
        principal="30.00",
        normal="5.00",
        late="1.00",
        fixed="2.00",
    )

    on_due = loan_installment_obligation(db, installment, date(2026, 2, 1))
    assert on_due.principal_due == Decimal("100.00")
    assert on_due.normal_price_interest_due == Decimal("20.00")
    assert on_due.fixed_penalty_due == ZERO
    assert on_due.late_interest_due == ZERO

    overdue = loan_installment_obligation(db, installment, date(2026, 2, 2))
    assert overdue.principal_due == Decimal("70.00")
    assert overdue.normal_price_interest_due == Decimal("15.00")
    assert overdue.fixed_penalty_due == Decimal("8.00")
    assert overdue.late_interest_due == Decimal("2.00")
    assert overdue.total_due == Decimal("95.00")
    db.close()


def test_adjustment_waits_until_first_affected_day_is_accrued():
    db = _db()
    member, _, installment = _installment(db)
    materialize_daily_late_interest(
        db,
        installment.id,
        through_date=date(2026, 1, 2),
        late_charge_effective_date=installment.due_date,
    )
    settlement = _settlement(
        db,
        member,
        installment,
        suffix="future-adjustment",
        confirmed_at=datetime(2026, 1, 2, 12, tzinfo=UTC),
        principal="50.00",
    )
    assert materialize_settlement_late_interest_adjustment(db, settlement.id) is None
    assert db.query(LoanLateChargeEvent).count() == 1
    db.close()


def _fixed_event_count(db, installment):
    return db.query(LoanLateChargeEvent).filter(
        LoanLateChargeEvent.loan_installment_id == installment.id,
        LoanLateChargeEvent.event_type == "FIXED_PENALTY_ASSESSED",
    ).count()


def test_fixed_penalty_starts_on_d_plus_one_once_and_never_grows():
    db = _db()
    _, _, installment = _installment(db)

    assert materialize_fixed_penalty(
        db,
        installment.id,
        through_date=installment.due_date,
        late_charge_effective_date=installment.due_date,
    ) is None
    assert _fixed_event_count(db, installment) == 0

    event = materialize_fixed_penalty(
        db,
        installment.id,
        through_date=installment.due_date + timedelta(days=1),
        late_charge_effective_date=installment.due_date,
    )
    assert event.amount == Decimal("10.00")
    assert event.effective_date == installment.due_date + timedelta(days=1)
    assert installment.fixed_penalty_amount == Decimal("10.00")

    repeated_ids = {event.id}
    for days in (1, 1, 2, 5, 30):
        repeated = materialize_fixed_penalty(
            db,
            installment.id,
            through_date=installment.due_date + timedelta(days=days),
            late_charge_effective_date=installment.due_date,
        )
        repeated_ids.add(repeated.id)
    assert repeated_ids == {event.id}
    assert _fixed_event_count(db, installment) == 1
    assert installment.fixed_penalty_amount == Decimal("10.00")
    db.close()


def test_fixed_penalty_none_and_pre_effective_installments_are_inactive():
    db = _db()
    _, _, installment = _installment(db)
    for _ in range(3):
        assert materialize_fixed_penalty(
            db,
            installment.id,
            through_date=installment.due_date + timedelta(days=30),
            late_charge_effective_date=None,
        ) is None
    assert materialize_fixed_penalty(
        db,
        installment.id,
        through_date=installment.due_date + timedelta(days=30),
        late_charge_effective_date=installment.due_date + timedelta(days=1),
    ) is None
    assert installment.late_charge_version is None
    assert _fixed_event_count(db, installment) == 0
    db.close()


def test_due_date_partial_principal_gets_one_penalty_not_in_interest_base():
    db = _db()
    member, _, installment = _installment(db)
    _settlement(
        db,
        member,
        installment,
        suffix="fixed-partial-due",
        confirmed_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
        principal="40.00",
    )

    penalty = materialize_fixed_penalty(
        db,
        installment.id,
        through_date=date(2026, 1, 2),
        late_charge_effective_date=installment.due_date,
    )
    accruals = materialize_daily_late_interest(
        db,
        installment.id,
        through_date=date(2026, 1, 2),
        late_charge_effective_date=installment.due_date,
    )
    assert penalty.amount == Decimal("10.00")
    assert accruals[0].eligible_principal == Decimal("60.00")
    assert accruals[0].amount == Decimal("0.40")
    assert late_interest_materialized(db, installment.id) == Decimal("0.40")
    assert _fixed_event_count(db, installment) == 1
    db.close()


def test_timely_full_payment_blocks_penalty_and_later_reversal_is_deferred():
    db = _db()
    member, user, installment = _installment(db)
    settlement = _settlement(
        db,
        member,
        installment,
        suffix="fixed-timely-full",
        confirmed_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
        principal="100.00",
    )
    assert materialize_fixed_penalty(
        db,
        installment.id,
        through_date=date(2026, 1, 2),
        late_charge_effective_date=installment.due_date,
    ) is None

    _reversal(
        db,
        user,
        installment,
        settlement,
        suffix="fixed-timely-full",
        reversed_at=datetime(2026, 1, 5, 12, tzinfo=UTC),
    )
    assert FIXED_PENALTY_AFTER_TIMELY_PAYMENT_REVERSAL == (
        "DEFERRED_BUSINESS_DECISION"
    )
    assert materialize_fixed_penalty(
        db,
        installment.id,
        through_date=date(2026, 1, 10),
        late_charge_effective_date=installment.due_date,
    ) is None
    assert _fixed_event_count(db, installment) == 0
    db.close()


def test_existing_penalty_survives_later_full_payment_and_reversal():
    db = _db()
    member, user, installment = _installment(db)
    original = materialize_fixed_penalty(
        db,
        installment.id,
        through_date=date(2026, 1, 2),
        late_charge_effective_date=installment.due_date,
    )
    settlement = _settlement(
        db,
        member,
        installment,
        suffix="fixed-after-assessment",
        confirmed_at=datetime(2026, 1, 3, 12, tzinfo=UTC),
        principal="100.00",
    )
    _reversal(
        db,
        user,
        installment,
        settlement,
        suffix="fixed-after-assessment",
        reversed_at=datetime(2026, 1, 5, 12, tzinfo=UTC),
    )
    repeated = materialize_fixed_penalty(
        db,
        installment.id,
        through_date=date(2026, 1, 30),
        late_charge_effective_date=installment.due_date,
    )
    assert repeated.id == original.id
    assert _fixed_event_count(db, installment) == 1
    db.close()


def test_fixed_penalty_uses_belem_civil_midnight():
    db = _db()
    _, _, installment = _installment(db)
    assert materialize_fixed_penalty(
        db,
        installment.id,
        through_date=datetime(2026, 1, 2, 2, 59, tzinfo=UTC),
        late_charge_effective_date=installment.due_date,
    ) is None
    event = materialize_fixed_penalty(
        db,
        installment.id,
        through_date=datetime(2026, 1, 2, 3, 0, tzinfo=UTC),
        late_charge_effective_date=installment.due_date,
    )
    assert event.effective_date == date(2026, 1, 2)
    db.close()


def test_read_model_components_settle_to_zero_without_negative_values():
    db = _db()
    member, _, installment = _installment(db)
    materialize_fixed_penalty(
        db,
        installment.id,
        through_date=date(2026, 1, 2),
        late_charge_effective_date=installment.due_date,
    )
    materialize_daily_late_interest(
        db,
        installment.id,
        through_date=date(2026, 1, 2),
        late_charge_effective_date=installment.due_date,
    )
    _settlement(
        db,
        member,
        installment,
        suffix="all-components-paid",
        confirmed_at=datetime(2026, 1, 2, 12, tzinfo=UTC),
        principal="100.00",
        normal="20.00",
        late="0.67",
        fixed="10.00",
    )
    obligation = loan_installment_obligation(db, installment, date(2026, 1, 2))
    assert obligation.principal_due == ZERO
    assert obligation.normal_price_interest_due == ZERO
    assert obligation.fixed_penalty_due == ZERO
    assert obligation.late_interest_due == ZERO
    assert obligation.total_due == ZERO
    db.close()


def test_zero_principal_has_zero_daily_interest_and_no_fixed_penalty():
    db = _db()
    member, _, installment = _installment(db)
    _settlement(
        db,
        member,
        installment,
        suffix="zero-principal",
        confirmed_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
        principal="100.00",
    )
    assert materialize_fixed_penalty(
        db,
        installment.id,
        through_date=date(2026, 1, 2),
        late_charge_effective_date=installment.due_date,
    ) is None
    events = materialize_daily_late_interest(
        db,
        installment.id,
        through_date=date(2026, 1, 2),
        late_charge_effective_date=installment.due_date,
    )
    assert len(events) == 1
    assert events[0].eligible_principal == ZERO
    assert events[0].amount == ZERO
    assert late_interest_materialized(db, installment.id) == ZERO
    db.close()


def test_sqlite_unique_constraint_is_fixed_penalty_last_defense():
    db = _db()
    _, _, installment = _installment(db)
    materialize_fixed_penalty(
        db,
        installment.id,
        through_date=date(2026, 1, 2),
        late_charge_effective_date=installment.due_date,
    )
    db.commit()
    db.add(
        LoanLateChargeEvent(
            loan_installment_id=installment.id,
            late_charge_version=LATE_CHARGE_VERSION,
            event_type="FIXED_PENALTY_ASSESSED",
            effective_date=date(2026, 1, 2),
            amount=Decimal("10.00"),
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
    assert _fixed_event_count(db, installment) == 1
    db.close()


ZERO = Decimal("0.00")
