"""Versioned runtime for loan-installment obligations and daily late interest.

The event ledger is authoritative.  Columns on ``LoanInstallment`` are kept
as rebuildable projections only.  Callers own the surrounding transaction;
these functions flush, but never commit.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.loan_rules import (
    LATE_CHARGE_SETTLEMENT_COMPONENT_VERSION,
    LATE_CHARGE_VERSION,
)
from app.models import (
    LoanInstallment,
    LoanLateChargeEvent,
    PaymentReversal,
    PaymentSettlement,
)
from app.services.late_charge_v1 import (
    PrincipalSegment,
    calculate_fixed_penalty,
    calculate_late_interest,
    financial_civil_date,
    is_late_charge_eligible,
)


CENT = Decimal("0.01")
ZERO = Decimal("0.00")
FIXED_PENALTY_EFFECTIVE_DATE_SEMANTICS = "DUE_DATE_PLUS_ONE_CIVIL_DAY"
FIXED_PENALTY_AFTER_TIMELY_PAYMENT_REVERSAL = "DEFERRED_BUSINESS_DECISION"

FIXED_PENALTY_ASSESSED = "FIXED_PENALTY_ASSESSED"
LATE_INTEREST_ACCRUED = "LATE_INTEREST_ACCRUED"
LATE_INTEREST_ADJUSTMENT_INCREASE = "LATE_INTEREST_ADJUSTMENT_INCREASE"
LATE_INTEREST_ADJUSTMENT_DECREASE = "LATE_INTEREST_ADJUSTMENT_DECREASE"


def _money(value: Decimal | int | str | None) -> Decimal:
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def _stored_financial_date(value: datetime) -> date:
    # SQLite drops the timezone marker on reload.  Datetimes persisted by this
    # application are UTC, so restore that marker before converting to Belem.
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return financial_civil_date(value)


def _as_financial_date(value: date | datetime) -> date:
    return financial_civil_date(value)


def _is_postgresql(db: Session) -> bool:
    return db.bind is not None and db.bind.dialect.name == "postgresql"


def _lock_installment(db: Session, installment_id: int) -> LoanInstallment:
    query = db.query(LoanInstallment).filter(LoanInstallment.id == installment_id)
    if _is_postgresql(db):
        query = query.with_for_update().populate_existing()
    installment = query.one_or_none()
    if installment is None:
        raise ValueError("Parcela de empréstimo não encontrada.")
    return installment


def _settlements(db: Session, installment_id: int) -> list[PaymentSettlement]:
    return (
        db.query(PaymentSettlement)
        .filter(
            PaymentSettlement.obligation_type == "LOAN_INSTALLMENT",
            PaymentSettlement.loan_installment_id == installment_id,
        )
        .order_by(PaymentSettlement.confirmed_at, PaymentSettlement.id)
        .all()
    )


def _reversal_by_settlement(
    db: Session, settlement_ids: list[int]
) -> dict[int, PaymentReversal]:
    if not settlement_ids:
        return {}
    rows = (
        db.query(PaymentReversal)
        .filter(PaymentReversal.settlement_id.in_(settlement_ids))
        .all()
    )
    return {row.settlement_id: row for row in rows}


def _late_principal_movements(
    db: Session,
    installment_id: int,
) -> list[tuple[date, Decimal]]:
    """Return principal-payment deltas on their late-base effective dates.

    A settlement on P and a reversal on R begin affecting the base on P+1 and
    R+1 respectively. Positive deltas reduce the base; negative deltas
    restore it.
    """

    settlements = _settlements(db, installment_id)
    reversals = _reversal_by_settlement(db, [row.id for row in settlements])
    movements = []
    for settlement in settlements:
        principal = _money(settlement.principal_applied)
        movements.append(
            (
                _stored_financial_date(settlement.confirmed_at)
                + timedelta(days=1),
                principal,
            )
        )
        reversal = reversals.get(settlement.id)
        if reversal is not None:
            restored = _money(reversal.principal_applied)
            if restored != principal:
                raise ValueError(
                    "Reversão possui componente de principal incompatível."
                )
            movements.append(
                (
                    _stored_financial_date(reversal.reversed_at)
                    + timedelta(days=1),
                    -restored,
                )
            )
    return sorted(movements, key=lambda item: item[0])


def _principal_paid_through(
    movements: list[tuple[date, Decimal]], on_date: date
) -> Decimal:
    paid = sum(
        (
            amount
            for effective_date, amount in movements
            if effective_date <= on_date
        ),
        ZERO,
    )
    return max(ZERO, _money(paid))


def _daily_late_schedule(
    db: Session,
    installment: LoanInstallment,
    through_date: date,
) -> list[tuple[date, Decimal, Decimal]]:
    """Return day, eligible principal and cumulative rounded interest."""

    movements = _late_principal_movements(db, installment.id)
    segments = []
    schedule = []
    day = installment.due_date + timedelta(days=1)
    while day <= through_date:
        paid = _principal_paid_through(movements, day)
        principal = max(ZERO, _money(installment.principal) - paid)
        segments.append(PrincipalSegment(principal=principal, days=1))
        schedule.append((day, principal, calculate_late_interest(segments)))
        day += timedelta(days=1)
    return schedule


def reconstruct_late_principal_base(
    db: Session,
    installment: LoanInstallment,
    financial_date: date | datetime,
) -> Decimal:
    """Reconstruct overdue unpaid principal from authoritative components."""

    on_date = _as_financial_date(financial_date)
    if on_date <= installment.due_date:
        return ZERO
    movements = _late_principal_movements(db, installment.id)
    return max(
        ZERO,
        _money(installment.principal)
        - _principal_paid_through(movements, on_date),
    )


def _expected_late_interest(
    db: Session,
    installment: LoanInstallment,
    through_date: date,
) -> Decimal:
    schedule = _daily_late_schedule(db, installment, through_date)
    return schedule[-1][2] if schedule else ZERO


def late_interest_materialized(
    db: Session,
    installment_id: int,
    *,
    through_date: date | None = None,
) -> Decimal:
    query = db.query(LoanLateChargeEvent).filter(
        LoanLateChargeEvent.loan_installment_id == installment_id,
        LoanLateChargeEvent.late_charge_version == LATE_CHARGE_VERSION,
    )
    if through_date is not None:
        query = query.filter(LoanLateChargeEvent.effective_date <= through_date)
    total = ZERO
    for event in query.all():
        amount = _money(event.amount)
        if event.event_type in {
            LATE_INTEREST_ACCRUED,
            LATE_INTEREST_ADJUSTMENT_INCREASE,
        }:
            total += amount
        elif event.event_type == LATE_INTEREST_ADJUSTMENT_DECREASE:
            total -= amount
    if total < ZERO:
        raise ValueError("Mora materializada não pode ser negativa.")
    return _money(total)


def _initialize_projection(installment: LoanInstallment) -> None:
    if installment.late_charge_version is None:
        installment.late_charge_version = LATE_CHARGE_VERSION
        installment.fixed_penalty_amount = ZERO
        installment.paid_fixed_penalty_amount = ZERO
        installment.late_interest_amount = ZERO
        installment.paid_late_interest_amount = ZERO
    elif installment.late_charge_version != LATE_CHARGE_VERSION:
        raise ValueError("Versão de mora da parcela é incompatível.")


def _event_query(
    db: Session,
    installment_id: int,
    event_type: str,
    *,
    effective_date: date | None = None,
    payment_settlement_id: int | None = None,
    payment_reversal_id: int | None = None,
):
    query = db.query(LoanLateChargeEvent).filter(
        LoanLateChargeEvent.loan_installment_id == installment_id,
        LoanLateChargeEvent.late_charge_version == LATE_CHARGE_VERSION,
        LoanLateChargeEvent.event_type == event_type,
    )
    if effective_date is not None:
        query = query.filter(LoanLateChargeEvent.effective_date == effective_date)
    if payment_settlement_id is not None:
        query = query.filter(
            LoanLateChargeEvent.payment_settlement_id == payment_settlement_id
        )
    if payment_reversal_id is not None:
        query = query.filter(
            LoanLateChargeEvent.payment_reversal_id == payment_reversal_id
        )
    return query


def _insert_unique_event(db: Session, **values) -> tuple[LoanLateChargeEvent, bool]:
    event_type = values["event_type"]
    query = _event_query(
        db,
        values["loan_installment_id"],
        event_type,
        effective_date=(
            values.get("effective_date")
            if event_type == LATE_INTEREST_ACCRUED
            else None
        ),
        payment_settlement_id=values.get("payment_settlement_id"),
        payment_reversal_id=values.get("payment_reversal_id"),
    )
    existing = query.one_or_none()
    if existing is not None:
        return existing, False
    try:
        with db.begin_nested():
            event = LoanLateChargeEvent(**values)
            db.add(event)
            db.flush()
        return event, True
    except IntegrityError:
        existing = query.populate_existing().one_or_none()
        if existing is None:
            raise
        return existing, False


def materialize_fixed_penalty(
    db: Session,
    installment_id: int,
    *,
    through_date: date | datetime,
    late_charge_effective_date: date | None,
) -> LoanLateChargeEvent | None:
    """Assess the fixed penalty once on the first civil overdue day."""

    target_date = _as_financial_date(through_date)
    if late_charge_effective_date is None:
        return None

    installment = _lock_installment(db, installment_id)
    if not is_late_charge_eligible(
        due_date=installment.due_date,
        effective_date=late_charge_effective_date,
    ):
        return None

    penalty_effective_date = installment.due_date + timedelta(days=1)
    if target_date < penalty_effective_date:
        return None

    existing = _event_query(
        db, installment.id, FIXED_PENALTY_ASSESSED
    ).one_or_none()
    if existing is not None:
        _initialize_projection(installment)
        installment.fixed_penalty_amount = _money(existing.amount)
        db.flush()
        return existing

    eligible_principal = reconstruct_late_principal_base(
        db, installment, penalty_effective_date
    )
    if eligible_principal <= ZERO:
        return None

    event, _ = _insert_unique_event(
        db,
        loan_installment_id=installment.id,
        late_charge_version=LATE_CHARGE_VERSION,
        event_type=FIXED_PENALTY_ASSESSED,
        effective_date=penalty_effective_date,
        amount=calculate_fixed_penalty(already_assessed=False),
        eligible_principal=None,
    )
    _initialize_projection(installment)
    installment.fixed_penalty_amount = _money(event.amount)
    db.flush()
    return event


def materialize_daily_late_interest(
    db: Session,
    installment_id: int,
    *,
    through_date: date | datetime,
    late_charge_effective_date: date | None,
) -> list[LoanLateChargeEvent]:
    """Materialize normal daily accruals through a caller-supplied civil date."""

    target_date = _as_financial_date(through_date)
    if late_charge_effective_date is None:
        return []

    installment = _lock_installment(db, installment_id)
    if not is_late_charge_eligible(
        due_date=installment.due_date,
        effective_date=late_charge_effective_date,
    ):
        return []
    if target_date <= installment.due_date:
        return []

    _initialize_projection(installment)
    existing_days = [
        row.effective_date
        for row in _event_query(db, installment.id, LATE_INTEREST_ACCRUED).all()
    ]
    last_normal_day = max(existing_days, default=None)
    start = installment.due_date + timedelta(days=1)
    if last_normal_day is not None:
        start = max(start, last_normal_day + timedelta(days=1))

    ledger_deltas = {}
    ledger_events = db.query(LoanLateChargeEvent).filter(
        LoanLateChargeEvent.loan_installment_id == installment.id,
        LoanLateChargeEvent.late_charge_version == LATE_CHARGE_VERSION,
        LoanLateChargeEvent.effective_date <= target_date,
    ).all()
    for event in ledger_events:
        delta = ZERO
        if event.event_type in {
            LATE_INTEREST_ACCRUED,
            LATE_INTEREST_ADJUSTMENT_INCREASE,
        }:
            delta = _money(event.amount)
        elif event.event_type == LATE_INTEREST_ADJUSTMENT_DECREASE:
            delta = -_money(event.amount)
        ledger_deltas[event.effective_date] = _money(
            ledger_deltas.get(event.effective_date, ZERO) + delta
        )

    created = []
    running = ZERO
    for day, principal, expected in _daily_late_schedule(
        db, installment, target_date
    ):
        running = _money(running + ledger_deltas.get(day, ZERO))
        if running < ZERO:
            raise ValueError("Mora materializada não pode ser negativa.")
        if day < start:
            continue
        amount = max(ZERO, _money(expected - running))
        event, was_created = _insert_unique_event(
            db,
            loan_installment_id=installment.id,
            late_charge_version=LATE_CHARGE_VERSION,
            event_type=LATE_INTEREST_ACCRUED,
            effective_date=day,
            amount=amount,
            eligible_principal=principal,
        )
        running = _money(running + _money(event.amount))
        if was_created:
            created.append(event)

    all_days = [
        row.effective_date
        for row in _event_query(db, installment.id, LATE_INTEREST_ACCRUED).all()
    ]
    if all_days:
        largest = max(all_days)
        if (
            installment.late_interest_accrued_through_date is None
            or largest > installment.late_interest_accrued_through_date
        ):
            installment.late_interest_accrued_through_date = largest
    installment.late_interest_amount = late_interest_materialized(
        db, installment.id
    )
    db.flush()
    return created


def _adjustment_effective_date(value: datetime) -> date:
    return _stored_financial_date(value) + timedelta(days=1)


def _materialize_adjustment(
    db: Session,
    installment: LoanInstallment,
    *,
    event_type: str,
    effective_date: date,
    payment_settlement_id: int | None = None,
    payment_reversal_id: int | None = None,
) -> LoanLateChargeEvent | None:
    cursor = installment.late_interest_accrued_through_date
    if cursor is None or effective_date > cursor:
        return None
    existing = _event_query(
        db,
        installment.id,
        event_type,
        payment_settlement_id=payment_settlement_id,
        payment_reversal_id=payment_reversal_id,
    ).one_or_none()
    if existing is not None:
        return existing

    current = late_interest_materialized(db, installment.id, through_date=cursor)
    expected = _expected_late_interest(db, installment, cursor)
    if event_type == LATE_INTEREST_ADJUSTMENT_DECREASE:
        amount = max(ZERO, _money(current - expected))
        amount = min(amount, current)
    else:
        amount = max(ZERO, _money(expected - current))
    event, _ = _insert_unique_event(
        db,
        loan_installment_id=installment.id,
        late_charge_version=LATE_CHARGE_VERSION,
        event_type=event_type,
        effective_date=effective_date,
        amount=amount,
        eligible_principal=None,
        payment_settlement_id=payment_settlement_id,
        payment_reversal_id=payment_reversal_id,
    )
    installment.late_interest_amount = late_interest_materialized(db, installment.id)
    db.flush()
    return event


def materialize_settlement_late_interest_adjustment(
    db: Session, settlement_id: int
) -> LoanLateChargeEvent | None:
    settlement = db.get(PaymentSettlement, settlement_id)
    if (
        settlement is None
        or settlement.obligation_type != "LOAN_INSTALLMENT"
        or settlement.loan_installment_id is None
    ):
        raise ValueError("Settlement não referencia uma parcela de empréstimo.")
    installment = _lock_installment(db, settlement.loan_installment_id)
    if installment.late_charge_version != LATE_CHARGE_VERSION:
        return None
    return _materialize_adjustment(
        db,
        installment,
        event_type=LATE_INTEREST_ADJUSTMENT_DECREASE,
        effective_date=_adjustment_effective_date(settlement.confirmed_at),
        payment_settlement_id=settlement.id,
    )


def materialize_reversal_late_interest_adjustment(
    db: Session, reversal_id: int
) -> LoanLateChargeEvent | None:
    reversal = db.get(PaymentReversal, reversal_id)
    if reversal is None:
        raise ValueError("Reversão não encontrada.")
    settlement = db.get(PaymentSettlement, reversal.settlement_id)
    if (
        settlement is None
        or settlement.obligation_type != "LOAN_INSTALLMENT"
        or settlement.loan_installment_id is None
    ):
        raise ValueError("Reversão não referencia uma parcela de empréstimo.")
    installment = _lock_installment(db, settlement.loan_installment_id)
    if installment.late_charge_version != LATE_CHARGE_VERSION:
        return None
    return _materialize_adjustment(
        db,
        installment,
        event_type=LATE_INTEREST_ADJUSTMENT_INCREASE,
        effective_date=_adjustment_effective_date(reversal.reversed_at),
        payment_reversal_id=reversal.id,
    )


def _active_settlements(
    db: Session,
    installment_id: int,
    financial_date: date,
) -> list[PaymentSettlement]:
    settlements = _settlements(db, installment_id)
    reversals = _reversal_by_settlement(db, [row.id for row in settlements])
    active = []
    for settlement in settlements:
        if _stored_financial_date(settlement.confirmed_at) > financial_date:
            continue
        reversal = reversals.get(settlement.id)
        if reversal is not None and _stored_financial_date(reversal.reversed_at) <= financial_date:
            continue
        active.append(settlement)
    return active


@dataclass(frozen=True)
class LoanInstallmentObligation:
    principal_due: Decimal
    normal_price_interest_due: Decimal
    fixed_penalty_due: Decimal
    late_interest_due: Decimal
    total_due: Decimal


def loan_installment_obligation(
    db: Session,
    installment: LoanInstallment,
    financial_date: date | datetime,
) -> LoanInstallmentObligation:
    """Return the four authoritative components due on a financial date."""

    on_date = _as_financial_date(financial_date)
    if on_date < installment.due_date:
        return LoanInstallmentObligation(ZERO, ZERO, ZERO, ZERO, ZERO)

    settlements = _active_settlements(db, installment.id, on_date)
    principal_paid = sum(
        (_money(row.principal_applied) for row in settlements), ZERO
    )
    normal_interest_paid = sum(
        (
            _money(row.normal_interest_applied)
            if row.settlement_component_version
            == LATE_CHARGE_SETTLEMENT_COMPONENT_VERSION
            else _money(row.interest_applied)
            for row in settlements
        ),
        ZERO,
    )
    fixed_paid = sum(
        (
            _money(row.fixed_penalty_applied)
            for row in settlements
            if row.settlement_component_version
            == LATE_CHARGE_SETTLEMENT_COMPONENT_VERSION
        ),
        ZERO,
    )
    late_paid = sum(
        (
            _money(row.late_interest_applied)
            for row in settlements
            if row.settlement_component_version
            == LATE_CHARGE_SETTLEMENT_COMPONENT_VERSION
        ),
        ZERO,
    )

    principal_due = max(ZERO, _money(installment.principal) - principal_paid)
    normal_due = max(ZERO, _money(installment.interest) - normal_interest_paid)
    fixed_assessed = sum(
        (
            _money(row.amount)
            for row in _event_query(
                db, installment.id, FIXED_PENALTY_ASSESSED
            ).filter(LoanLateChargeEvent.effective_date <= on_date)
        ),
        ZERO,
    )
    fixed_due = max(ZERO, _money(fixed_assessed) - fixed_paid)
    late_due = max(
        ZERO,
        late_interest_materialized(db, installment.id, through_date=on_date)
        - late_paid,
    )
    total = _money(principal_due + normal_due + fixed_due + late_due)
    return LoanInstallmentObligation(
        principal_due=_money(principal_due),
        normal_price_interest_due=_money(normal_due),
        fixed_penalty_due=_money(fixed_due),
        late_interest_due=_money(late_due),
        total_due=total,
    )


__all__ = [
    "FIXED_PENALTY_AFTER_TIMELY_PAYMENT_REVERSAL",
    "FIXED_PENALTY_EFFECTIVE_DATE_SEMANTICS",
    "LoanInstallmentObligation",
    "late_interest_materialized",
    "loan_installment_obligation",
    "materialize_daily_late_interest",
    "materialize_fixed_penalty",
    "materialize_reversal_late_interest_adjustment",
    "materialize_settlement_late_interest_adjustment",
    "reconstruct_late_principal_base",
]
