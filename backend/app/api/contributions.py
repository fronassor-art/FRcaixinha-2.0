from datetime import date, datetime, timezone
import calendar
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models import User, Member, Contribution, Payment, Quota, Group
from app.schemas.finance import ContributionIn
from app.api.deps import current_user
from app.services.payment_settlement import contribution_financial_status

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
    return {
        "id": c.id,
        "competence": c.competence.isoformat(),
        "amount": str(c.amount),
        "status": _status(c),
        "due_date": c.due_date.isoformat() if c.due_date else None,
        "paid_amount": str(_paid_amount(c)),
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
    pending = sum((Decimal(c.amount) for c in rows if c.status != "PAID"), Decimal("0"))
    group = db.get(Group, member.group_id)
    expected = (Decimal(group.monthly_amount) * group.months) if group else Decimal("0")
    return {
        "member_id": member.id,
        "quota_units": str(member.quota.units if member.quota else Decimal("0")),
        "paid_total": str(paid.quantize(Decimal("0.01"))),
        "pending_total": str(pending.quantize(Decimal("0.01"))),
        "expected_total": str(expected.quantize(Decimal("0.01"))),
        "paid_count": sum(1 for c in rows if c.status == "PAID"),
        "pending_count": sum(1 for c in rows if c.status != "PAID"),
    }

@router.get("/{contribution_id}")
def get_contribution(contribution_id: int, user: User=Depends(current_user), db: Session=Depends(get_db)):
    member = _member_or_403(user, db)
    c = db.get(Contribution, contribution_id)
    if not c or c.member_id != member.id:
        raise HTTPException(404, "Contribuição não encontrada.")
    return _serialize(c, db)
