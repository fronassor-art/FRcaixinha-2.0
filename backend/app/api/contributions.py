from datetime import date, datetime, timezone
import calendar
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models import User, Member, Contribution, ContributionChargeEvent, CycleParticipation, Payment, Quota, Group
from app.schemas.finance import ContributionIn
from app.api.deps import current_user
from app.services.payment_settlement import contribution_financial_status
from app.services.cycle_foundation import ensure_contributions_for_entry
from app.services.cycle_participation import CycleParticipationError, voluntary_exit

router = APIRouter(prefix="/contributions", tags=["contributions"])

def _member_or_403(user: User, db: Session):
    member = db.query(Member).filter(Member.user_id == user.id, Member.status == "ACTIVE").first()
    if not member:
        raise HTTPException(403, "Usuário não é membro ativo.")
    return member

def _paid_amount(c: Contribution):
    return Decimal(c.paid_amount or (c.amount if c.status == "PAID" else 0)).quantize(Decimal("0.01"))

def _status(c: Contribution):
    return contribution_financial_status(c, _paid_amount(c), datetime.now(timezone.utc))

def _due_date(competence: date, due_day: int | None):
    if due_day is None:
        return None
    return date(competence.year, competence.month, min(max(1, int(due_day)), calendar.monthrange(competence.year, competence.month)[1]))

def _serialize(c: Contribution, db: Session):
    payment = db.get(Payment, c.payment_id) if c.payment_id else None
    charge = db.query(ContributionChargeEvent).filter(
        ContributionChargeEvent.contribution_id == c.id
    ).order_by(ContributionChargeEvent.accrued_through.desc(), ContributionChargeEvent.id.desc()).first()
    return {
        "id": c.id,
        "competence": c.competence.isoformat(),
        "amount": str(c.amount),
        "status": _status(c),
        "due_date": c.due_date.isoformat() if c.due_date else None,
        "paid_amount": str(_paid_amount(c)),
        "principal_outstanding": str(Decimal("0.00") if c.cancelled_at else max(Decimal("0.00"), Decimal(c.amount) - _paid_amount(c))),
        "cancelled_at": c.cancelled_at.isoformat() if c.cancelled_at else None,
        "cancellation_reason": c.cancellation_reason,
        "late_charge": None if charge is None else {
            "fixed_penalty": str(charge.fixed_penalty),
            "daily_interest": str(charge.daily_interest),
            "accrued_through": charge.accrued_through.isoformat(),
            "rule_version": charge.rule_version,
            "frozen": charge.event_type == "BLOCK_FREEZE",
        },
        "payment": None if not payment else {
            "id": payment.id,
            "provider": payment.provider,
            "provider_payment_id": payment.provider_payment_id,
            "status": payment.status,
            "created_at": payment.created_at.isoformat(),
        },
    }

@router.post("")
def create_contribution(data: ContributionIn, user: User=Depends(current_user), db: Session=Depends(get_db)):
    member = _member_or_403(user, db)
    if data.cycle_id is not None:
        try:
            rows = ensure_contributions_for_entry(
                db, member_id=member.id, cycle_id=data.cycle_id,
                entry_date=data.entry_date or data.competence,
            )
            db.commit()
            return {"items": [_serialize(row, db) for row in rows]}
        except ValueError as exc:
            db.rollback()
            raise HTTPException(400, str(exc)) from exc
    if data.amount is None:
        raise HTTPException(422, "amount is required for legacy contribution flow")
    existing = db.query(Contribution).filter(
        Contribution.member_id == member.id, Contribution.competence == data.competence
    ).first()
    if existing:
        raise HTTPException(409, "Contribuição já existe para esta competência.")
    group = db.get(Group, member.group_id)
    c = Contribution(member_id=member.id, competence=data.competence, amount=data.amount, status="PENDING", due_date=_due_date(data.competence, group.due_day if group else None), paid_amount=Decimal("0.00"))
    db.add(c); db.commit(); db.refresh(c)
    return _serialize(c, db)

@router.get("")
def list_contributions(user: User=Depends(current_user), db: Session=Depends(get_db)):
    member = _member_or_403(user, db)
    rows = db.query(Contribution).filter(Contribution.member_id == member.id).order_by(Contribution.competence.desc()).all()
    return {"items": [_serialize(c, db) for c in rows]}

@router.get("/summary")
def contribution_summary(user: User=Depends(current_user), db: Session=Depends(get_db)):
    member = _member_or_403(user, db)
    rows = db.query(Contribution).filter(Contribution.member_id == member.id).all()
    paid = sum((Decimal(c.amount) for c in rows if c.status == "PAID"), Decimal("0"))
    pending = sum((Decimal(c.amount) - _paid_amount(c) for c in rows if c.status != "PAID" and c.cancelled_at is None), Decimal("0"))
    group = db.get(Group, member.group_id)
    expected = (Decimal(group.monthly_amount) * group.months) if group else Decimal("0")
    return {
        "member_id": member.id,
        "quota_units": str(member.quota.units if member.quota else Decimal("0")),
        "paid_total": str(paid.quantize(Decimal("0.01"))),
        "pending_total": str(pending.quantize(Decimal("0.01"))),
        "expected_total": str(expected.quantize(Decimal("0.01"))),
        "paid_count": sum(1 for c in rows if c.status == "PAID"),
        "pending_count": sum(1 for c in rows if c.status != "PAID" and c.cancelled_at is None),
    }

@router.get("/cycles/{cycle_id}/participation")
def cycle_participation(cycle_id: int, user: User=Depends(current_user), db: Session=Depends(get_db)):
    member = _member_or_403(user, db)
    row = db.query(CycleParticipation).filter_by(
        cycle_id=cycle_id, member_id=member.id
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "Participação no ciclo não encontrada.")
    return {
        "cycle_id": row.cycle_id, "member_id": row.member_id, "status": row.status,
        "joined_at": row.joined_at.isoformat() if row.joined_at else None,
        "voluntary_exit_at": row.voluntary_exit_at.isoformat() if row.voluntary_exit_at else None,
        "blocked_at": row.blocked_at.isoformat() if row.blocked_at else None,
        "block_reason": row.block_reason,
    }


@router.post("/cycles/{cycle_id}/exit")
def exit_cycle(cycle_id: int, user: User=Depends(current_user), db: Session=Depends(get_db)):
    member = _member_or_403(user, db)
    try:
        row = voluntary_exit(
            db, member_id=member.id, cycle_id=cycle_id, actor_user_id=user.id
        )
        db.commit()
        return {"cycle_id": cycle_id, "member_id": member.id, "status": row.status,
                "voluntary_exit_at": row.voluntary_exit_at.isoformat()}
    except CycleParticipationError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.get("/{contribution_id}")
def get_contribution(contribution_id: int, user: User=Depends(current_user), db: Session=Depends(get_db)):
    member = _member_or_403(user, db)
    c = db.get(Contribution, contribution_id)
    if not c or c.member_id != member.id:
        raise HTTPException(404, "Contribuição não encontrada.")
    return _serialize(c, db)
