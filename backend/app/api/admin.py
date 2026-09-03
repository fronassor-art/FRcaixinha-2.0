from datetime import date
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.api.deps import require_admin
from app.schemas.finance import LedgerReversalIn
from app.services.ledger import reverse_entry, verify_ledger_chain
from app.db.session import get_db
from app.models import User, Member, Group, Quota, Contribution, Loan, LoanInstallment, LedgerEntry, AuditLog

router = APIRouter(prefix="/admin", tags=["admin"])


def money(value):
    return str(Decimal(value or 0).quantize(Decimal("0.01")))


def audit(db, actor_id, action, entity_type, entity_id, details=None):
    db.add(AuditLog(actor_user_id=actor_id, action=action, entity_type=entity_type,
                    entity_id=str(entity_id), details=details))


@router.get("/dashboard")
def dashboard(admin=Depends(require_admin), db: Session = Depends(get_db)):
    members_active = db.query(Member).filter(Member.status == "ACTIVE").count()
    quotas_active = db.query(Quota).filter(Quota.status == "ACTIVE").count()
    contributions_paid = db.query(func.coalesce(func.sum(Contribution.amount), 0)).filter(Contribution.status == "PAID").scalar()
    contributions_pending = db.query(func.coalesce(func.sum(Contribution.amount), 0)).filter(Contribution.status != "PAID").scalar()

    loan_counts = {}
    for status in ["REQUESTED", "APPROVED", "REJECTED", "ACTIVE", "PAID", "CANCELLED"]:
        loan_counts[status.lower()] = db.query(Loan).filter(Loan.status == status).count()

    overdue = db.query(LoanInstallment).filter(
        LoanInstallment.due_date < date.today(), LoanInstallment.status != "PAID"
    ).all()
    open_installments = db.query(LoanInstallment).filter(LoanInstallment.status != "PAID").all()
    outstanding = sum((Decimal(i.amount) - Decimal(i.paid_amount or 0) for i in open_installments), Decimal("0"))
    interest_expected = sum((Decimal(i.interest) for i in db.query(LoanInstallment).all()), Decimal("0"))
    interest_received = sum((Decimal(i.interest) for i in db.query(LoanInstallment).filter(LoanInstallment.status == "PAID").all()), Decimal("0"))

    credits = db.query(func.coalesce(func.sum(LedgerEntry.amount), 0)).filter(LedgerEntry.direction == "CREDIT").scalar()
    debits = db.query(func.coalesce(func.sum(LedgerEntry.amount), 0)).filter(LedgerEntry.direction == "DEBIT").scalar()
    balance = Decimal(credits or 0) - Decimal(debits or 0)

    return {
        "members": {"active": members_active, "total": db.query(Member).count()},
        "quotas": {"active": quotas_active, "total_units": money(db.query(func.coalesce(func.sum(Quota.units), 0)).scalar())},
        "contributions": {"paid_total": money(contributions_paid), "pending_total": money(contributions_pending)},
        "loans": loan_counts,
        "overdue_installments": len(overdue),
        "outstanding_loan_balance": money(outstanding),
        "interest_expected": money(interest_expected),
        "interest_received": money(interest_received),
        "ledger": {"credits": money(credits), "debits": money(debits), "balance": money(balance)},
    }


@router.get("/members")
def members(status: str | None = None, q: str | None = Query(default=None, min_length=1),
            admin=Depends(require_admin), db: Session = Depends(get_db)):
    query = db.query(Member).options(joinedload(Member.user), joinedload(Member.quota))
    if status:
        query = query.filter(Member.status == status.upper())
    if q:
        term = f"%{q}%"
        query = query.join(Member.user).filter(or_(User.name.ilike(term), User.email.ilike(term), User.cpf.ilike(term)))
    rows = query.order_by(Member.id.desc()).all()
    return {"items": [{
        "id": m.id, "status": m.status, "joined_at": m.joined_at.isoformat(),
        "user": {"id": m.user.id, "name": m.user.name, "email": m.user.email, "cpf": m.user.cpf, "phone": m.user.phone, "is_active": m.user.is_active},
        "group_id": m.group_id,
        "quota_units": money(m.quota.units if m.quota else 0),
    } for m in rows]}


@router.get("/members/{member_id}")
def member_detail(member_id: int, admin=Depends(require_admin), db: Session = Depends(get_db)):
    m = db.query(Member).options(joinedload(Member.user), joinedload(Member.quota)).filter(Member.id == member_id).first()
    if not m:
        raise HTTPException(404, "Membro não encontrado.")
    contributions = db.query(Contribution).filter(Contribution.member_id == m.id).order_by(Contribution.competence.desc()).all()
    loans = db.query(Loan).filter(Loan.member_id == m.id).order_by(Loan.id.desc()).all()
    return {
        "id": m.id, "status": m.status, "group_id": m.group_id,
        "user": {"id": m.user.id, "name": m.user.name, "email": m.user.email, "cpf": m.user.cpf, "phone": m.user.phone, "is_active": m.user.is_active},
        "quota_units": money(m.quota.units if m.quota else 0),
        "contributions": [{"id": c.id, "competence": c.competence.isoformat(), "amount": money(c.amount), "status": c.status} for c in contributions],
        "loans": [{"id": l.id, "principal": money(l.principal), "monthly_rate": str(l.monthly_rate), "installments": l.installments, "status": l.status} for l in loans],
    }


@router.get("/contributions")
def admin_contributions(status: str | None = None, competence: date | None = None,
                        member_id: int | None = None, admin=Depends(require_admin), db: Session = Depends(get_db)):
    query = db.query(Contribution)
    # Avoid relying on an undeclared ORM relationship; join explicitly when needed.
    if member_id is not None:
        query = query.filter(Contribution.member_id == member_id)
    if status:
        query = query.filter(Contribution.status == status.upper())
    if competence:
        query = query.filter(Contribution.competence == competence)
    rows = query.order_by(Contribution.competence.desc(), Contribution.id.desc()).all()
    member_map = {m.id: m for m in db.query(Member).filter(Member.id.in_([r.member_id for r in rows])).all()} if rows else {}
    users = {u.id: u for u in db.query(User).filter(User.id.in_([member_map[r.member_id].user_id for r in rows if r.member_id in member_map])).all()} if rows else {}
    return {"items": [{
        "id": c.id, "member_id": c.member_id, "member_name": users.get(member_map[c.member_id].user_id).name if c.member_id in member_map else None,
        "competence": c.competence.isoformat(), "amount": money(c.amount), "status": c.status, "payment_id": c.payment_id
    } for c in rows]}


@router.get("/loans")
def admin_loans(status: str | None = None, member_id: int | None = None,
                admin=Depends(require_admin), db: Session = Depends(get_db)):
    query = db.query(Loan).order_by(Loan.id.desc())
    if status:
        query = query.filter(Loan.status == status.upper())
    if member_id is not None:
        query = query.filter(Loan.member_id == member_id)
    rows = query.all()
    member_map = {m.id: m for m in db.query(Member).filter(Member.id.in_([r.member_id for r in rows])).all()} if rows else {}
    users = {u.id: u for u in db.query(User).filter(User.id.in_([member_map[r.member_id].user_id for r in rows if r.member_id in member_map])).all()} if rows else {}
    return {"items": [{
        "id": l.id, "member_id": l.member_id, "member_name": users.get(member_map[l.member_id].user_id).name if l.member_id in member_map else None,
        "principal": money(l.principal), "monthly_rate": str(l.monthly_rate), "installments": l.installments,
        "status": l.status, "requested_at": l.requested_at.isoformat(), "decided_at": l.decided_at.isoformat() if l.decided_at else None
    } for l in rows]}


@router.get("/loans/{loan_id}")
def admin_loan_detail(loan_id: int, admin=Depends(require_admin), db: Session = Depends(get_db)):
    loan = db.get(Loan, loan_id)
    if not loan:
        raise HTTPException(404, "Empréstimo não encontrado.")
    member = db.get(Member, loan.member_id)
    user = db.get(User, member.user_id) if member else None
    installments = db.query(LoanInstallment).filter(LoanInstallment.loan_id == loan.id).order_by(LoanInstallment.number).all()
    return {
        "id": loan.id, "member_id": loan.member_id, "member_name": user.name if user else None,
        "principal": money(loan.principal), "monthly_rate": str(loan.monthly_rate), "installments": loan.installments,
        "status": loan.status, "requested_at": loan.requested_at.isoformat(),
        "decided_at": loan.decided_at.isoformat() if loan.decided_at else None,
        "items": [{"id": i.id, "number": i.number, "due_date": i.due_date.isoformat(), "principal": money(i.principal),
                   "interest": money(i.interest), "amount": money(i.amount), "paid_amount": money(i.paid_amount), "status": i.status} for i in installments]
    }


@router.get("/overdue-installments")
def overdue_installments(admin=Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(LoanInstallment).filter(LoanInstallment.due_date < date.today(), LoanInstallment.status != "PAID").order_by(LoanInstallment.due_date).all()
    loan_map = {l.id: l for l in db.query(Loan).filter(Loan.id.in_([i.loan_id for i in rows])).all()} if rows else {}
    member_map = {m.id: m for m in db.query(Member).filter(Member.id.in_([loan_map[i.loan_id].member_id for i in rows if i.loan_id in loan_map])).all()} if rows else {}
    user_map = {u.id: u for u in db.query(User).filter(User.id.in_([member_map[loan_map[i.loan_id].member_id].user_id for i in rows if i.loan_id in loan_map])).all()} if rows else {}
    return {"items": [{
        "id": i.id, "loan_id": i.loan_id, "member_name": user_map.get(member_map[loan_map[i.loan_id].member_id].user_id).name if i.loan_id in loan_map else None,
        "number": i.number, "due_date": i.due_date.isoformat(), "amount": money(i.amount), "paid_amount": money(i.paid_amount),
        "outstanding": money(Decimal(i.amount) - Decimal(i.paid_amount or 0)), "days_overdue": (date.today() - i.due_date).days
    } for i in rows]}


@router.get("/ledger/summary")
def ledger_summary(admin=Depends(require_admin), db: Session = Depends(get_db)):
    credits = Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount), 0)).filter(LedgerEntry.direction == "CREDIT").scalar() or 0)
    debits = Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount), 0)).filter(LedgerEntry.direction == "DEBIT").scalar() or 0)
    by_account = {}
    for account, direction, amount in db.query(LedgerEntry.account, LedgerEntry.direction, func.sum(LedgerEntry.amount)).group_by(LedgerEntry.account, LedgerEntry.direction).all():
        by_account.setdefault(account, {"credits": Decimal("0"), "debits": Decimal("0")})
        by_account[account]["credits" if direction == "CREDIT" else "debits"] = Decimal(amount or 0)
    return {"credits": money(credits), "debits": money(debits), "balance": money(credits - debits),
            "accounts": {k: {"credits": money(v["credits"]), "debits": money(v["debits"]), "balance": money(v["credits"] - v["debits"])} for k, v in by_account.items()}}


@router.get("/reports/cash-flow")
def cash_flow(admin=Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(LedgerEntry).order_by(LedgerEntry.created_at.desc()).limit(500).all()
    return {"items": [{"id": r.id, "account": r.account, "direction": r.direction, "amount": money(r.amount),
                       "reference_type": r.reference_type, "reference_id": r.reference_id, "created_at": r.created_at.isoformat()} for r in rows]}


@router.get("/audit-logs")
def audit_logs(limit: int = Query(default=100, ge=1, le=500), admin=Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()
    return {"items": [{"id": r.id, "actor_user_id": r.actor_user_id, "action": r.action, "entity_type": r.entity_type,
                       "entity_id": r.entity_id, "details": r.details, "created_at": r.created_at.isoformat()} for r in rows]}


@router.get("/ledger/integrity")
def ledger_integrity(admin=Depends(require_admin), db: Session = Depends(get_db)):
    return verify_ledger_chain(db)

@router.post("/ledger/{entry_id}/reverse")
def ledger_reverse(entry_id: int, payload: LedgerReversalIn, admin=Depends(require_admin), db: Session = Depends(get_db)):
    original = db.get(LedgerEntry, entry_id)
    if not original:
        raise HTTPException(404, "Lançamento não encontrado.")
    try:
        reversal = reverse_entry(db, original, payload.reason)
        audit(db, admin.id, "LEDGER_REVERSED", "LedgerEntry", entry_id, payload.reason)
        db.commit()
        db.refresh(reversal)
        return {"id": reversal.id, "reversal_of_id": reversal.reversal_of_id, "entry_hash": reversal.entry_hash, "status": "REVERSED"}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc))
