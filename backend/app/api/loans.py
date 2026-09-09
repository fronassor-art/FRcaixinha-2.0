from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models import User, Member, Loan, LoanInstallment, AuditLog
from app.schemas.finance import LoanRequestIn, LoanDecisionIn
from app.api.deps import current_user, require_admin
from app.services.notifications_v12 import create_notification
from app.services.loan_engine_v17 import add_months, money

router = APIRouter(prefix="/loans", tags=["loans"])

def _member_for(user, db):
    member = db.query(Member).filter(Member.user_id == user.id, Member.status == "ACTIVE").first()
    if not member:
        raise HTTPException(403, "Somente membro ativo pode acessar empréstimos.")
    return member

def _serialize(loan, db):
    installments = db.query(LoanInstallment).filter(LoanInstallment.loan_id == loan.id).order_by(LoanInstallment.number).all()
    return {"id": loan.id, "principal": str(loan.principal), "monthly_rate": str(loan.monthly_rate), "installments": loan.installments,
            "status": loan.status, "requested_at": loan.requested_at.isoformat() if loan.requested_at else None,
            "decided_at": loan.decided_at.isoformat() if loan.decided_at else None,
            "items": [{"id": i.id, "number": i.number, "due_date": i.due_date.isoformat(), "principal": str(i.principal),
                       "interest": str(i.interest), "amount": str(i.amount), "paid_amount": str(i.paid_amount or 0), "penalty_amount": str(i.penalty_amount or 0),
                       "remaining": str(money(i.amount) + money(i.penalty_amount) - money(i.paid_amount)), "status": i.status} for i in installments]}

@router.post("")
def request_loan(data: LoanRequestIn, user: User=Depends(current_user), db: Session=Depends(get_db)):
    member = _member_for(user, db)
    if data.principal <= 0 or data.installments <= 0 or data.installments > 120:
        raise HTTPException(400, "Dados do empréstimo inválidos.")
    loan = Loan(member_id=member.id, principal=data.principal, monthly_rate=data.monthly_rate, installments=data.installments)
    db.add(loan); db.commit(); db.refresh(loan)
    return {"id": loan.id, "status": loan.status}

@router.get("")
def list_my_loans(user: User=Depends(current_user), db: Session=Depends(get_db)):
    member = _member_for(user, db)
    loans = db.query(Loan).filter(Loan.member_id == member.id).order_by(Loan.id.desc()).all()
    return {"items": [{"id": l.id, "principal": str(l.principal), "monthly_rate": str(l.monthly_rate), "installments": l.installments, "status": l.status, "requested_at": l.requested_at.isoformat()} for l in loans]}

@router.get("/{loan_id}")
def my_loan(loan_id: int, user: User=Depends(current_user), db: Session=Depends(get_db)):
    member = _member_for(user, db)
    loan = db.get(Loan, loan_id)
    if not loan or loan.member_id != member.id:
        raise HTTPException(404, "Empréstimo não encontrado.")
    data = _serialize(loan, db); data["installments"] = data.pop("items")
    return data

@router.post("/{loan_id}/decision")
def decide_loan(loan_id: int, data: LoanDecisionIn, admin=Depends(require_admin), db: Session=Depends(get_db)):
    loan = db.get(Loan, loan_id)
    if not loan or loan.status != "REQUESTED": raise HTTPException(404, "Solicitação não encontrada ou já decidida.")
    loan.status = "APPROVED" if data.approve else "REJECTED"; loan.decided_by = admin.id
    from datetime import datetime, timezone
    loan.decided_at = datetime.now(timezone.utc)
    if data.approve:
        from app.services.approval_engine_v048 import assert_loan_approval_allowed
        try:
            assert_loan_approval_allowed(db, loan, admin.id, data.force_exception, data.admin_note)
        except ValueError as exc:
            detail = exc.args[0] if exc.args else str(exc)
            if isinstance(detail, dict):
                raise HTTPException(409, detail=detail)
            raise HTTPException(400, str(detail))
        # Parcelas mensais reais: a primeira vence um mês após a decisão.
        principal_each=(loan.principal/loan.installments).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
        interest_each=(loan.principal*loan.monthly_rate).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
        total=Decimal("0")
        base_date=date.today()
        for n in range(1,loan.installments+1):
            principal=principal_each if n<loan.installments else loan.principal-total
            total += principal
            db.add(LoanInstallment(loan_id=loan.id,number=n,due_date=add_months(base_date,n),principal=principal,interest=interest_each,amount=principal+interest_each))
    db.add(AuditLog(actor_user_id=admin.id,action="LOAN_DECISION",entity_type="LOAN",entity_id=str(loan.id),details=("approved" + ("; exception=" + data.admin_note if data.force_exception and data.admin_note else "")) if data.approve else "rejected"))
    member=db.get(Member,loan.member_id)
    if member: create_notification(db,member.user_id,"LOAN_DECISION","Empréstimo aprovado" if data.approve else "Empréstimo rejeitado","Sua solicitação de empréstimo foi aprovada." if data.approve else "Sua solicitação de empréstimo foi rejeitada.","LOAN",str(loan.id))
    db.commit(); return {"id":loan.id,"status":loan.status}


@router.post("/{loan_id}/release")
def release_loan(loan_id: int, admin=Depends(require_admin), db: Session=Depends(get_db)):
    from app.services.loan_engine_v17 import release_loan as do_release
    from app.services.risk_v036 import evaluate_release
    from app.services.approval_engine_v048 import evaluate_loan_pipeline
    loan = db.get(Loan, loan_id)
    if not loan:
        raise HTTPException(404, "Empréstimo não encontrado.")
    from app.models import Group
    member = db.get(Member, loan.member_id)
    group = db.get(Group, member.group_id) if member else None
    if not group:
        raise HTTPException(409, "Grupo do participante não encontrado.")
    # Lock the group row so concurrent releases cannot both pass the same risk limits.
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        group = db.query(Group).filter(Group.id == group.id).with_for_update().one()
    risk = evaluate_release(db, loan, group)
    if risk['status'] != 'PASS':
        raise HTTPException(409, detail={"code": "RISK_LIMIT_BLOCKED", "risk": risk})
    pipeline = evaluate_loan_pipeline(db, loan, persist_risk=True, include_release=True)
    if pipeline['decision'] != 'ALLOW':
        raise HTTPException(409, detail={"code": "FINANCIAL_APPROVAL_BLOCKED", "pipeline": pipeline})
    try:
        changed = do_release(db, loan, admin.id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    db.commit()
    return {"id": loan.id, "status": loan.status, "released": changed, "disbursed_at": loan.disbursed_at.isoformat() if loan.disbursed_at else None}
