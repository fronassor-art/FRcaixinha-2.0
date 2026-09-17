import hashlib
import json
import secrets
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models import (
    User,
    Member,
    Loan,
    LoanSimulation,
    LoanInstallment,
    AuditLog,
    CollectionAgreement,
    AgreementInstallment,
)
from app.schemas.finance import LoanRequestIn, LoanDecisionIn, LoanSimulationIn, LoanSimulationConfirmationIn
from app.api.deps import current_user, require_admin
from app.services.notifications_v12 import create_notification
from app.services.loan_engine_v17 import add_months, lock_loan, money, touch_loan
from app.services.loan_amortization import build_loan_simulation, calculate_linear_amortization
from app.services.loan_eligibility import evaluate_loan_eligibility
from app.services.member_financial import (
    get_member_financial_position,
    lock_member_financial_account,
)
from app.services.risk_v036 import cash_balance
from app.core.loan_rules import LOAN_CALCULATION_VERSION, LOAN_SIMULATION_TTL_MINUTES, OFFICIAL_LOAN_MONTHLY_RATE, validate_loan_installments


def _now_utc():
    return datetime.now(timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _simulation_for_token(db, token, *, lock=False):
    query = db.query(LoanSimulation).filter(LoanSimulation.token_hash == _token_hash(token))
    if lock and db.bind is not None and db.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    return query.first()


router = APIRouter(prefix="/loans", tags=["loans"])
@router.post("/simulations")
def simulate_loan(data: LoanSimulationIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    member = _member_for(user, db)
    try:
        validate_loan_installments(data.installments)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    simulation = build_loan_simulation(data.principal, OFFICIAL_LOAN_MONTHLY_RATE, data.installments)
    payload = {
        "calculation_version": simulation["calculation_version"],
        "principal": str(simulation["principal"]),
        "monthly_rate": str(simulation["monthly_rate"]),
        "installments": simulation["installments"],
        "installments_schedule": [
            {key: str(value) if key != "number" else value for key, value in row.items()}
            for row in simulation["installments_schedule"]
        ],
        "totals": {key: str(value) for key, value in simulation["totals"].items()},
    }
    token = secrets.token_urlsafe(32)
    now = _now_utc()
    snapshot_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    row = LoanSimulation(
        member_id=member.id, principal=simulation["principal"], monthly_rate=OFFICIAL_LOAN_MONTHLY_RATE,
        installments=data.installments, calculation_version=LOAN_CALCULATION_VERSION,
        schedule_json=snapshot_json, schedule_hash=hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
        token_hash=_token_hash(token), status="SIMULATED",
        expires_at=now + timedelta(minutes=LOAN_SIMULATION_TTL_MINUTES), created_at=now,
    )
    db.add(row)
    db.flush()
    db.add(AuditLog(actor_user_id=user.id, action="LOAN_SIMULATION_CREATED", entity_type="LOAN_SIMULATION", entity_id=str(row.id), details=row.schedule_hash))
    db.commit()
    return {"simulation_token": token, "expires_at": row.expires_at.isoformat(), **payload}


@router.post("/simulations/confirm")
def confirm_simulation(data: LoanSimulationConfirmationIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    member = _member_for(user, db)
    row = _simulation_for_token(db, data.simulation_token, lock=True)
    if row is None or row.member_id != member.id:
        raise HTTPException(404, "Simulação não encontrada.")
    if _as_utc(row.expires_at) <= _now_utc():
        raise HTTPException(409, "LOAN_SIMULATION_EXPIRED")
    if row.status == "CONSUMED":
        raise HTTPException(409, "LOAN_SIMULATION_ALREADY_CONSUMED")
    if row.status != "CONFIRMED":
        row.status = "CONFIRMED"
        row.confirmed_at = _now_utc()
        db.add(AuditLog(actor_user_id=user.id, action="LOAN_SIMULATION_CONFIRMED", entity_type="LOAN_SIMULATION", entity_id=str(row.id), details=row.schedule_hash))
        db.commit()
    return {"id": row.id, "status": row.status, "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None}


def _member_for(user, db):
    member = db.query(Member).filter(Member.user_id == user.id, Member.status == "ACTIVE").first()
    if not member:
        raise HTTPException(403, "Somente membro ativo pode acessar empréstimos.")
    return member

def _loan_eligibility(member, loan, db):
    from app.models import Group, Quota

    member, _account = lock_member_financial_account(db, member)

    group = db.get(Group, member.group_id)
    if group is None:
        raise HTTPException(409, "Grupo do participante não encontrado.")

    quota = (
        db.query(Quota)
        .filter(
            Quota.member_id == member.id,
            Quota.status == "ACTIVE",
        )
        .first()
    )

    position = get_member_financial_position(db, member)

    outstanding = Decimal("0.00")
    existing_loans = (
        db.query(Loan)
        .filter(
            Loan.member_id == member.id,
            Loan.status.in_(["ACTIVE", "OVERDUE", "IN_COLLECTION"]),
            Loan.id != loan.id,
        )
        .all()
    )

    for existing in existing_loans:
        settled = Decimal(
            existing.principal_settled_with_own_balance or 0
        )
        outstanding += max(
            Decimal("0.00"),
            Decimal(existing.principal or 0) - settled,
        )

    agreement_rows = (
        db.query(AgreementInstallment)
        .join(CollectionAgreement, CollectionAgreement.id == AgreementInstallment.agreement_id)
        .filter(
            CollectionAgreement.member_id == member.id,
            CollectionAgreement.status == "APPROVED",
        )
        .all()
    )
    agreement_open_balance = sum(
        (
            max(Decimal("0.00"), Decimal(row.principal or 0) - Decimal(row.paid_amount or 0))
            + max(Decimal("0.00"), Decimal(row.penalty_amount or 0) - Decimal(row.paid_penalty_amount or 0))
            for row in agreement_rows
        ),
        Decimal("0.00"),
    )

    if quota and group.max_quota_multiple is not None:
        normal_limit = money(
            Decimal(quota.units) * Decimal(group.max_quota_multiple)
        )
    else:
        normal_limit = Decimal("0.00")

    liquidity = cash_balance(db)

    result = evaluate_loan_eligibility(
        own_balance=position["own_balance"],
        committed_balance=position["committed_balance"],
        outstanding_principal=money(outstanding),
        normal_credit_limit=normal_limit,
        requested_amount=money(loan.principal),
        liquidity_available=liquidity,
    )
    if agreement_open_balance > Decimal("0.00") and result.eligible:
        return replace(
            result,
            eligible=False,
            decision="RENEGOCIACAO",
            reason="Existe dívida aberta; operação deve passar por renegociação.",
        )
    return result

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
    if data.principal <= 0:
        raise HTTPException(400, "Dados do empréstimo inválidos.")
    try:
        validate_loan_installments(data.installments)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    simulation = _simulation_for_token(db, data.simulation_token, lock=True)
    if simulation is None or simulation.member_id != member.id:
        raise HTTPException(409, "LOAN_SIMULATION_NOT_FOUND")
    if _as_utc(simulation.expires_at) <= _now_utc():
        raise HTTPException(409, "LOAN_SIMULATION_EXPIRED")
    if simulation.status == "CONSUMED":
        raise HTTPException(409, "LOAN_SIMULATION_ALREADY_CONSUMED")
    if simulation.status != "CONFIRMED" or simulation.confirmed_at is None:
        raise HTTPException(409, "LOAN_SIMULATION_NOT_CONFIRMED")
    if (
        Decimal(simulation.principal) != Decimal(data.principal)
        or simulation.installments != data.installments
        or Decimal(simulation.monthly_rate) != OFFICIAL_LOAN_MONTHLY_RATE
        or simulation.calculation_version != LOAN_CALCULATION_VERSION
    ):
        raise HTTPException(409, "LOAN_SIMULATION_TERMS_MISMATCH")
    loan = Loan(
        member_id=member.id,
        principal=data.principal,
        monthly_rate=OFFICIAL_LOAN_MONTHLY_RATE,
        installments=data.installments,
    )

    eligibility = _loan_eligibility(member, loan, db)

    if eligibility.decision == "NEGADO":
        raise HTTPException(
            409,
            detail={
                "code": "LOAN_NOT_ELIGIBLE",
                "decision": eligibility.decision,
                "reason": eligibility.reason,
                "available_balance": str(eligibility.available_balance),
                "liquidity_available": str(eligibility.liquidity_available),
            },
        )

    db.add(loan)
    db.flush()
    simulation.status = "CONSUMED"
    simulation.consumed_at = _now_utc()
    simulation.loan_id = loan.id
    db.add(AuditLog(actor_user_id=user.id, action="LOAN_SIMULATION_CONSUMED", entity_type="LOAN_SIMULATION", entity_id=str(simulation.id), details=str(loan.id)))
    db.add(simulation)
    db.commit()
    db.refresh(loan)

    return {
        "id": loan.id,
        "status": loan.status,
        "eligibility": {
            "eligible": eligibility.eligible,
            "decision": eligibility.decision,
            "reason": eligibility.reason,
            "own_balance": str(eligibility.own_balance),
            "committed_balance": str(eligibility.committed_balance),
            "available_balance": str(eligibility.available_balance),
            "outstanding_principal": str(eligibility.outstanding_principal),
            "normal_credit_limit": str(eligibility.normal_credit_limit),
            "requested_amount": str(eligibility.requested_amount),
            "liquidity_available": str(eligibility.liquidity_available),
        },
    }

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
    if not loan: raise HTTPException(404, "Solicitação não encontrada ou já decidida.")
    loan = lock_loan(db, loan)
    if loan.status != "REQUESTED": raise HTTPException(404, "Solicitação não encontrada ou já decidida.")
    loan.status = "APPROVED" if data.approve else "REJECTED"; loan.decided_by = admin.id
    touch_loan(loan)
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
        # Parcelas mensais reais: juros sobre o saldo devedor,
        # com amortização linear do principal.
        rows, _, _ = calculate_linear_amortization(
            loan.principal,
            loan.monthly_rate,
            loan.installments,
        )
        base_date = date.today()

        for row in rows:
            db.add(LoanInstallment(
                loan_id=loan.id,
                number=row["number"],
                due_date=add_months(base_date, row["number"]),
                principal=row["principal"],
                interest=row["interest"],
                amount=row["amount"],
            ))
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
