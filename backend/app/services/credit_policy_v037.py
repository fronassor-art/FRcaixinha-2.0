from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import func, or_
from datetime import date, timedelta
from sqlalchemy.orm import Session
from app.models import Group, Member, Loan, LoanInstallment, Quota

CENT = Decimal("0.01")
def money(v):
    return Decimal(v or 0).quantize(CENT, rounding=ROUND_HALF_UP)

def evaluate_credit_policy(db: Session, loan: Loan, group: Group):
    member = db.get(Member, loan.member_id)
    errors = []
    checks = []
    if not member:
        return {"status":"BLOCKED","score":0,"checks":[{"rule":"MEMBER_EXISTS","status":"FAIL","detail":"Participante não encontrado."}],"errors":["Participante não encontrado."]}

    simultaneous = db.query(func.count(Loan.id)).filter(Loan.member_id==member.id, Loan.status.in_(["ACTIVE","APPROVED"]), Loan.id != loan.id).scalar() or 0
    checks.append({"rule":"MAX_SIMULTANEOUS_LOANS","status":"PASS" if simultaneous < group.max_simultaneous_loans else "FAIL","actual":simultaneous,"limit":group.max_simultaneous_loans})
    if simultaneous >= group.max_simultaneous_loans:
        errors.append("Limite de empréstimos simultâneos excedido.")

    checks.append({"rule":"MAX_INSTALLMENTS","status":"PASS" if loan.installments <= group.max_installments else "FAIL","actual":loan.installments,"limit":group.max_installments})
    if loan.installments > group.max_installments: errors.append("Quantidade máxima de parcelas excedida.")

    cutoff = date.today() - timedelta(days=int(group.grace_days or 0))
    overdue = db.query(func.count(LoanInstallment.id)).join(Loan, Loan.id==LoanInstallment.loan_id).filter(Loan.member_id==member.id, LoanInstallment.paid_at.is_(None), LoanInstallment.due_date < cutoff).scalar() or 0
    checks.append({"rule":"MAX_OVERDUE_INSTALLMENTS","status":"PASS" if overdue <= group.max_overdue_installments else "FAIL","actual":overdue,"limit":group.max_overdue_installments})
    if overdue > group.max_overdue_installments: errors.append("Quantidade de parcelas em atraso excede a política.")

    paid = db.query(func.count(LoanInstallment.id)).join(Loan, Loan.id==LoanInstallment.loan_id).filter(Loan.member_id==member.id, LoanInstallment.paid_at.is_not(None)).scalar() or 0
    late_paid = db.query(func.count(LoanInstallment.id)).join(Loan, Loan.id==LoanInstallment.loan_id).filter(Loan.member_id==member.id, LoanInstallment.paid_at.is_not(None), LoanInstallment.last_penalty_date.is_not(None)).scalar() or 0
    ratio = Decimal(1) if paid == 0 else Decimal(paid-late_paid)/Decimal(paid)
    if group.min_on_time_ratio is None:
        checks.append({"rule":"MIN_ON_TIME_RATIO","status":"NOT_CONFIGURED","actual":str(ratio)})
    else:
        ok = ratio >= Decimal(group.min_on_time_ratio)
        checks.append({"rule":"MIN_ON_TIME_RATIO","status":"PASS" if ok else "FAIL","actual":str(ratio),"limit":str(group.min_on_time_ratio)})
        if not ok: errors.append("Índice mínimo de pontualidade não atendido.")

    if member.declared_monthly_income is None:
        checks.append({"rule":"INSTALLMENT_INCOME_RATIO","status":"NOT_CONFIGURED","actual":None})
    else:
        installment_estimate = money((loan.principal / Decimal(loan.installments)) + (loan.principal * Decimal(loan.monthly_rate)))
        ratio_income = Decimal(0) if member.declared_monthly_income == 0 else installment_estimate / Decimal(member.declared_monthly_income)
        if group.max_installment_income_ratio is None:
            checks.append({"rule":"INSTALLMENT_INCOME_RATIO","status":"NOT_CONFIGURED","actual":str(ratio_income)})
        else:
            ok = ratio_income <= Decimal(group.max_installment_income_ratio)
            checks.append({"rule":"INSTALLMENT_INCOME_RATIO","status":"PASS" if ok else "FAIL","actual":str(ratio_income),"limit":str(group.max_installment_income_ratio)})
            if not ok: errors.append("Parcela estimada excede o comprometimento máximo de renda configurado.")

    quota = db.query(Quota).filter(Quota.member_id==member.id, Quota.status=="ACTIVE").first()
    if quota and group.max_quota_multiple is not None:
        limit = money(Decimal(quota.units) * Decimal(group.max_quota_multiple))
        ok = money(loan.principal) <= limit
        checks.append({"rule":"MAX_QUOTA_MULTIPLE","status":"PASS" if ok else "FAIL","actual":str(money(loan.principal)),"limit":str(limit)})
        if not ok: errors.append("Valor solicitado excede o múltiplo máximo da quota.")
    else:
        checks.append({"rule":"MAX_QUOTA_MULTIPLE","status":"NOT_CONFIGURED"})

    passed = sum(1 for c in checks if c["status"]=="PASS")
    applicable = sum(1 for c in checks if c["status"] in ("PASS","FAIL"))
    score = round((passed/applicable)*100) if applicable else 100
    return {"status":"PASS" if not errors else "BLOCKED","score":score,"checks":checks,"errors":errors}
