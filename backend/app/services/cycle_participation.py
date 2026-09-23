"""Cycle-scoped membership transitions; callers own the transaction."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.models import (
    AgreementInstallment, AuditLog, CollectionAgreement, Contribution,
    ContributionChargeEvent, CycleParticipation, Loan, LoanInstallment,
    Member, Payment, PaymentSettlement, Quota,
)
from app.services.contribution_charge_v1 import RULE_VERSION, calculate_contribution_charges
from app.services.late_charge_v1 import financial_civil_date
from app.services.loan_engine_v17 import installment_due
from app.services.member_financial import lock_member_financial_account

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
BLOCK_REASON = "THREE_CONSECUTIVE_OVERDUE"


class CycleParticipationError(ValueError):
    pass


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def _effective_at(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise CycleParticipationError("effective_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _participation(
    db: Session, member_id: int, cycle_id: int, *, create: bool,
    entry_date: date | None = None,
) -> CycleParticipation | None:
    query = db.query(CycleParticipation).filter(
        CycleParticipation.member_id == member_id,
        CycleParticipation.cycle_id == cycle_id,
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        query = query.with_for_update().populate_existing()
    row = query.one_or_none()
    if row is not None or not create:
        return row
    if db.query(Quota.id).filter(Quota.member_id == member_id, Quota.cycle_id == cycle_id).first() is None:
        raise CycleParticipationError("member has no quota in cycle")
    if entry_date is None:
        joined_at = datetime.now(timezone.utc)
    else:
        try:
            financial_zone = ZoneInfo("America/Belem")
        except ZoneInfoNotFoundError:
            financial_zone = timezone(timedelta(hours=-3))
        joined_at = datetime.combine(entry_date, time.min, financial_zone).astimezone(timezone.utc)
    row = CycleParticipation(
        member_id=member_id, cycle_id=cycle_id, status="ACTIVE", joined_at=joined_at
    )
    db.add(row)
    db.flush()
    return row


def ensure_active_participation(
    db: Session, *, member_id: int, cycle_id: int, entry_date: date | None = None,
) -> CycleParticipation:
    # Member is the shared financial mutex used by settlement and reversal.
    lock_member_financial_account(db, member_id)
    row = _participation(db, member_id, cycle_id, create=True, entry_date=entry_date)
    if row.status != "ACTIVE":
        raise CycleParticipationError("participation is closed for this cycle")
    return row


def _paid(c: Contribution) -> Decimal:
    return _money(c.paid_amount if c.paid_amount is not None else (c.amount if c.status == "PAID" else 0))


def _month_index(value: date) -> int:
    return value.year * 12 + value.month


def consecutive_overdue(contributions: list[Contribution], as_of: date) -> bool:
    streak = 0
    previous = None
    for c in sorted(contributions, key=lambda item: item.competence):
        if c.cancelled_at is not None or c.due_date is None:
            streak = 0
            previous = None
            continue
        month = _month_index(c.competence)
        if month == previous:
            raise CycleParticipationError("duplicate monthly competence")
        overdue = c.due_date < as_of and _paid(c) < _money(c.amount)
        streak = streak + 1 if overdue and previous is not None and month == previous + 1 else (1 if overdue else 0)
        if streak >= 3:
            return True
        previous = month
    return False


def _payment_history(db: Session, c: Contribution, through: date) -> tuple[tuple[date, Decimal], ...]:
    settlements = db.query(PaymentSettlement).filter(
        PaymentSettlement.contribution_id == c.id,
    ).order_by(PaymentSettlement.confirmed_at, PaymentSettlement.id).all()
    history = []
    for settlement in settlements:
        reversal = settlement.payment_reversal
        if reversal is not None and financial_civil_date(
            reversal.reversed_at.replace(tzinfo=timezone.utc) if reversal.reversed_at.tzinfo is None else reversal.reversed_at
        ) <= through:
            continue
        confirmed = settlement.confirmed_at
        if confirmed.tzinfo is None:
            confirmed = confirmed.replace(tzinfo=timezone.utc)
        history.append((financial_civil_date(confirmed), _money(settlement.principal_applied)))
    current = _paid(c)
    recorded = sum((amount for _, amount in history), ZERO)
    if recorded != current:
        if not settlements and current == _money(c.amount) and c.paid_at is not None:
            paid_at = c.paid_at.replace(tzinfo=timezone.utc) if c.paid_at.tzinfo is None else c.paid_at
            return ((financial_civil_date(paid_at), current),)
        if not settlements and c.payment_id is not None:
            payment = db.get(Payment, c.payment_id)
            if (
                payment is not None and payment.status == "approved"
                and payment.confirmed_at is not None
                and (payment.reference_type or "").upper() in ("", "CONTRIBUTION")
                and (not payment.reference_id or payment.reference_id == str(c.id))
                and _money(payment.amount_received if payment.amount_received is not None else payment.amount) == current
            ):
                confirmed = payment.confirmed_at
                if confirmed.tzinfo is None:
                    confirmed = confirmed.replace(tzinfo=timezone.utc)
                return ((financial_civil_date(confirmed), current),)
        raise CycleParticipationError(f"contribution {c.id} has no complete dated principal history")
    return tuple(history)


def _cancel(c: Contribution, *, when: datetime, reason: str) -> None:
    if c.cancelled_at is not None or _paid(c) >= _money(c.amount):
        return
    c.cancelled_at = when
    c.cancellation_reason = reason
    c.status = "CANCELLED"


def evaluate_delinquency(
    db: Session, *, member_id: int, cycle_id: int,
    effective_at: datetime | None = None,
) -> CycleParticipation:
    when = _effective_at(effective_at)
    civil = financial_civil_date(when)
    lock_member_financial_account(db, member_id)
    row = _participation(db, member_id, cycle_id, create=True)
    if row.status != "ACTIVE":
        return row
    contributions = db.query(Contribution).filter(
        Contribution.member_id == member_id, Contribution.cycle_id == cycle_id,
    ).order_by(Contribution.competence, Contribution.id).all()
    if not consecutive_overdue(contributions, civil):
        return row
    snapshots = []
    for c in contributions:
        if c.due_date is None:
            raise CycleParticipationError(f"contribution {c.id} has no due date")
        history = _payment_history(db, c, civil)
        charges = calculate_contribution_charges(
            principal=_money(c.amount), due_date=c.due_date, through=civil,
            principal_payments=history,
        )
        principal_open = max(ZERO, _money(c.amount) - _paid(c))
        if charges.fixed_penalty or charges.daily_interest or principal_open:
            snapshots.append((c, charges, principal_open))
    row.status = "BLOCKED_DELINQUENCY"
    row.blocked_at = when
    row.block_reason = BLOCK_REASON
    for c, charges, principal_open in snapshots:
        db.add(ContributionChargeEvent(
            contribution_id=c.id, participation_id=row.id,
            event_type="BLOCK_FREEZE", rule_version=RULE_VERSION,
            accrued_through=civil, fixed_penalty=charges.fixed_penalty,
            daily_interest=charges.daily_interest,
            cancelled_principal=principal_open,
        ))
        _cancel(c, when=when, reason="BLOCKED_DELINQUENCY")
    db.add(AuditLog(
        actor_user_id=None, action="CYCLE_PARTICIPATION_BLOCKED",
        entity_type="CYCLE_PARTICIPATION", entity_id=str(row.id),
        details=f"cycle_id={cycle_id};member_id={member_id};financial_date={civil.isoformat()};reason={BLOCK_REASON}",
    ))
    db.flush()
    return row


def materialize_active_charges(
    db: Session, *, member_id: int, cycle_id: int,
    effective_at: datetime | None = None,
) -> int:
    """Append cumulative charge snapshots without modifying past assessments."""
    when = _effective_at(effective_at)
    civil = financial_civil_date(when)
    lock_member_financial_account(db, member_id)
    row = _participation(db, member_id, cycle_id, create=True)
    if row.status != "ACTIVE":
        return 0
    created = 0
    contributions = db.query(Contribution).filter(
        Contribution.member_id == member_id, Contribution.cycle_id == cycle_id,
        Contribution.due_date < civil, Contribution.cancelled_at.is_(None),
    ).all()
    for c in contributions:
        charges = calculate_contribution_charges(
            principal=_money(c.amount), due_date=c.due_date, through=civil,
            principal_payments=_payment_history(db, c, civil),
        )
        if charges.fixed_penalty == ZERO and charges.daily_interest == ZERO:
            continue
        prior = db.query(ContributionChargeEvent).filter(
            ContributionChargeEvent.contribution_id == c.id,
            ContributionChargeEvent.event_type == "ACCRUAL_SNAPSHOT",
        ).order_by(ContributionChargeEvent.accrued_through.desc()).first()
        if prior is not None and (
            _money(prior.fixed_penalty), _money(prior.daily_interest)
        ) == (charges.fixed_penalty, charges.daily_interest):
            continue
        db.add(ContributionChargeEvent(
            contribution_id=c.id, participation_id=row.id,
            event_type="ACCRUAL_SNAPSHOT", rule_version=RULE_VERSION,
            accrued_through=civil, fixed_penalty=charges.fixed_penalty,
            daily_interest=charges.daily_interest, cancelled_principal=ZERO,
        ))
        created += 1
    db.flush()
    return created


def voluntary_exit(
    db: Session, *, member_id: int, cycle_id: int,
    effective_at: datetime | None = None, actor_user_id: int | None = None,
) -> CycleParticipation:
    when = _effective_at(effective_at)
    civil = financial_civil_date(when)
    lock_member_financial_account(db, member_id)
    row = _participation(db, member_id, cycle_id, create=True)
    if row.status != "ACTIVE":
        raise CycleParticipationError("participation is closed for this cycle")
    contributions = db.query(Contribution).filter(
        Contribution.member_id == member_id, Contribution.cycle_id == cycle_id,
    ).all()
    if any(c.cancelled_at is None and c.due_date is not None and c.due_date < civil and _paid(c) < _money(c.amount) for c in contributions):
        raise CycleParticipationError("member has overdue contributions")
    for c in contributions:
        if c.due_date is None or c.due_date >= civil or c.cancelled_at is not None:
            continue
        charges = calculate_contribution_charges(
            principal=_money(c.amount), due_date=c.due_date, through=civil,
            principal_payments=_payment_history(db, c, civil),
        )
        if charges.fixed_penalty > ZERO or charges.daily_interest > ZERO:
            raise CycleParticipationError("member has unpaid contribution late charges")
    loan_ids = [loan.id for loan in db.query(Loan).filter(Loan.member_id == member_id)]
    if loan_ids and any(
        item.due_date < when.date() and installment_due(item) > ZERO
        for item in db.query(LoanInstallment).filter(LoanInstallment.loan_id.in_(loan_ids))
    ):
        raise CycleParticipationError("member has overdue loan installments")
    if db.query(AgreementInstallment.id).join(
        CollectionAgreement, AgreementInstallment.agreement_id == CollectionAgreement.id
    ).filter(
        CollectionAgreement.member_id == member_id,
        CollectionAgreement.status == "APPROVED",
        AgreementInstallment.due_date < civil,
        AgreementInstallment.status != "PAID",
    ).first() is not None:
        raise CycleParticipationError("member has overdue agreement installments")
    row.status = "VOLUNTARILY_EXITED"
    row.voluntary_exit_at = when
    for c in contributions:
        _cancel(c, when=when, reason="VOLUNTARY_EXIT")
    db.add(AuditLog(
        actor_user_id=actor_user_id, action="CYCLE_PARTICIPATION_VOLUNTARY_EXIT",
        entity_type="CYCLE_PARTICIPATION", entity_id=str(row.id),
        details=f"cycle_id={cycle_id};member_id={member_id};financial_date={civil.isoformat()}",
    ))
    db.flush()
    return row
