from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from calendar import monthrange
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models import User, Member, Contribution, Loan, LoanInstallment, LedgerEntry, Expense

ZERO = Decimal("0.00")

def money(v):
    return str(Decimal(v or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def month_bounds(competence: date):
    start = competence.replace(day=1)
    end = competence.replace(day=monthrange(competence.year, competence.month)[1])
    return start, end

def monthly_report(db: Session, competence: date):
    start, end = month_bounds(competence)
    contributions = Decimal(db.query(func.coalesce(func.sum(Contribution.amount), 0)).filter(
        Contribution.status == "PAID", Contribution.competence.between(start, end)
    ).scalar() or 0)
    expenses = Decimal(db.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
        Expense.status == "POSTED", Expense.expense_date.between(start, end)
    ).scalar() or 0)
    installments = db.query(LoanInstallment).filter(LoanInstallment.status == "PAID").all()
    interest_received = sum((Decimal(i.interest) for i in installments if _paid_in_period(i, start, end)), ZERO)
    credits = Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount), 0)).filter(
        LedgerEntry.direction == "CREDIT", LedgerEntry.created_at >= start
    ).filter(LedgerEntry.created_at < end.fromordinal(end.toordinal()+1)).scalar() or 0)
    debits = Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount), 0)).filter(
        LedgerEntry.direction == "DEBIT", LedgerEntry.created_at >= start
    ).filter(LedgerEntry.created_at < end.fromordinal(end.toordinal()+1)).scalar() or 0)
    return {
        "competence": start.isoformat(),
        "period_end": end.isoformat(),
        "contributions_paid": money(contributions),
        "expenses": money(expenses),
        "interest_received": money(interest_received),
        "operating_result": money(contributions + interest_received - expenses),
        "ledger_credits_in_period": money(credits),
        "ledger_debits_in_period": money(debits),
    }

def _paid_in_period(i, start, end):
    # v0.10 does not add a schema column solely for a report. Payment timing is
    # approximated by the installment due date when no paid_at exists.
    return start <= i.due_date <= end

def member_statement(db: Session, member_id: int):
    member = db.get(Member, member_id)
    if not member:
        return None
    user = db.get(User, member.user_id)
    contributions = db.query(Contribution).filter(Contribution.member_id == member_id).order_by(Contribution.competence.desc()).all()
    loans = db.query(Loan).filter(Loan.member_id == member_id).order_by(Loan.id.desc()).all()
    loan_ids = [l.id for l in loans]
    installments = db.query(LoanInstallment).filter(LoanInstallment.loan_id.in_(loan_ids)).order_by(LoanInstallment.due_date).all() if loan_ids else []
    total_contrib = sum((Decimal(c.amount) for c in contributions if c.status == "PAID"), ZERO)
    total_paid_loans = sum((Decimal(i.paid_amount or 0) for i in installments), ZERO)
    outstanding = sum((max(ZERO, Decimal(i.amount) - Decimal(i.paid_amount or 0)) for i in installments if i.status != "PAID"), ZERO)
    return {
        "member": {"id": member.id, "name": user.name, "email": user.email, "cpf": user.cpf, "phone": user.phone, "status": member.status, "group_id": member.group_id},
        "totals": {"contributions_paid": money(total_contrib), "loan_payments": money(total_paid_loans), "loan_outstanding": money(outstanding)},
        "contributions": [{"id": c.id, "competence": c.competence.isoformat(), "amount": money(c.amount), "status": c.status} for c in contributions],
        "loans": [{"id": l.id, "principal": money(l.principal), "monthly_rate": str(l.monthly_rate), "installments": l.installments, "status": l.status} for l in loans],
        "installments": [{"id": i.id, "loan_id": i.loan_id, "number": i.number, "due_date": i.due_date.isoformat(), "amount": money(i.amount), "paid_amount": money(i.paid_amount), "outstanding": money(max(ZERO, Decimal(i.amount)-Decimal(i.paid_amount or 0))), "status": i.status} for i in installments],
    }

def loan_report(db: Session):
    loans = db.query(Loan).order_by(Loan.id.desc()).all()
    result = []
    for l in loans:
        m = db.get(Member, l.member_id); u = db.get(User, m.user_id) if m else None
        inst = db.query(LoanInstallment).filter(LoanInstallment.loan_id == l.id).all()
        outstanding = sum((max(ZERO, Decimal(i.amount)-Decimal(i.paid_amount or 0)) for i in inst), ZERO)
        interest = sum((Decimal(i.interest) for i in inst), ZERO)
        result.append({"loan_id": l.id, "member_id": l.member_id, "member_name": u.name if u else None, "principal": money(l.principal), "status": l.status, "installments": l.installments, "interest_total": money(interest), "outstanding": money(outstanding)})
    return {"items": result}

def delinquency_report(db: Session):
    today = date.today()
    rows = db.query(LoanInstallment).filter(LoanInstallment.due_date < today, LoanInstallment.status != "PAID").order_by(LoanInstallment.due_date).all()
    items=[]
    total=ZERO
    for i in rows:
        l=db.get(Loan,i.loan_id); m=db.get(Member,l.member_id) if l else None; u=db.get(User,m.user_id) if m else None
        outstanding=max(ZERO, Decimal(i.amount)-Decimal(i.paid_amount or 0)); total += outstanding
        items.append({"installment_id":i.id,"loan_id":i.loan_id,"member_name":u.name if u else None,"number":i.number,"due_date":i.due_date.isoformat(),"days_overdue":(today-i.due_date).days,"outstanding":money(outstanding)})
    return {"as_of":today.isoformat(),"count":len(items),"total_outstanding":money(total),"items":items}
