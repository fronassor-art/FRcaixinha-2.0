from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy.orm import Session

from app.models import Loan, LoanInstallment, LedgerEntry, AuditLog, Member
from app.services.ledger import post_entry

CENT = Decimal('0.01')

def money(v):
    return Decimal(v or 0).quantize(CENT, rounding=ROUND_HALF_UP)

def add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(d.day, monthrange(year, month)[1]))

def installment_due(inst):
    penalty_open = max(Decimal("0.00"), money(Decimal(inst.penalty_amount or 0) - Decimal(getattr(inst, "paid_penalty_amount", 0) or 0)))
    base_open = max(Decimal("0.00"), money(Decimal(inst.amount) - Decimal(inst.paid_amount or 0)))
    return money(base_open + penalty_open)

def apply_payment(inst, amount: Decimal):
    amount = money(amount)
    penalty_open = max(Decimal("0.00"), money(Decimal(inst.penalty_amount or 0) - Decimal(getattr(inst, "paid_penalty_amount", 0) or 0)))
    base_open = max(Decimal("0.00"), money(Decimal(inst.amount) - Decimal(inst.paid_amount or 0)))
    due = money(penalty_open + base_open)
    applied = min(amount, due)
    penalty_applied = min(penalty_open, applied)
    base_applied = min(base_open, money(applied - penalty_applied))
    inst.paid_penalty_amount = money(Decimal(getattr(inst, "paid_penalty_amount", 0) or 0) + penalty_applied)
    inst.paid_amount = money(Decimal(inst.paid_amount or 0) + base_applied)
    inst.status = 'PAID' if installment_due(inst) <= 0 else 'PARTIAL'
    if inst.status == 'PAID' and getattr(inst, 'paid_at', None) is None:
        inst.paid_at = datetime.now(timezone.utc)
    return {'applied': applied, 'penalty_applied': penalty_applied, 'base_applied': base_applied, 'excess': money(amount-applied), 'status': inst.status}

def calculate_daily_penalty(inst, on_date: date, daily_rate: Decimal):
    daily_rate = Decimal(daily_rate or 0)
    if daily_rate < 0:
        raise ValueError('daily_rate não pode ser negativo')
    if inst.status == 'PAID' or on_date <= inst.due_date:
        return Decimal('0.00')
    last = inst.last_penalty_date or inst.due_date
    start = max(last + timedelta(days=1), inst.due_date + timedelta(days=1))
    days = max(0, (on_date - start).days + 1)
    if days == 0 or daily_rate == 0:
        return Decimal('0.00')
    base = max(Decimal('0.00'), money(Decimal(inst.amount) - Decimal(inst.paid_amount or 0)))
    increment = money(base * daily_rate * Decimal(days))
    inst.penalty_amount = money(Decimal(inst.penalty_amount or 0) + increment)
    inst.last_penalty_date = on_date
    return increment

def ensure_loan_completion(db: Session, loan: Loan):
    installments = db.query(LoanInstallment).filter(LoanInstallment.loan_id == loan.id).all()
    if installments and all(i.status == 'PAID' for i in installments):
        loan.status = 'PAID'
        loan.paid_at = datetime.now(timezone.utc)
        return True
    return False

def release_loan(db: Session, loan: Loan, admin_id: int):
    if loan.status != 'APPROVED':
        raise ValueError('Somente empréstimos aprovados podem ser liberados.')
    exists = db.query(LedgerEntry).filter(LedgerEntry.reference_type == 'LOAN_DISBURSEMENT', LedgerEntry.reference_id == str(loan.id)).first()
    if exists:
        loan.status = 'ACTIVE'
        if loan.disbursed_at is None:
            loan.disbursed_at = exists.created_at
        return False
    post_entry(db, 'CAIXINHA', 'DEBIT', money(loan.principal), 'LOAN_DISBURSEMENT', str(loan.id))
    loan.status = 'ACTIVE'
    loan.disbursed_at = datetime.now(timezone.utc)
    db.add(AuditLog(actor_user_id=admin_id, action='LOAN_RELEASE', entity_type='LOAN', entity_id=str(loan.id), details='funds released'))
    member = db.get(Member, loan.member_id)
    if member:
        from app.services.notifications_v12 import create_notification
        create_notification(db, member.user_id, 'LOAN_RELEASED', 'Empréstimo liberado', 'Seu empréstimo foi liberado e as parcelas já estão disponíveis para pagamento.', 'LOAN', str(loan.id))
    return True

def accrue_overdue_penalties(db: Session, on_date: date, daily_rate: Decimal):
    rows = db.query(LoanInstallment).filter(LoanInstallment.due_date < on_date, LoanInstallment.status != 'PAID').all()
    total = Decimal('0.00')
    changed = 0
    for inst in rows:
        inc = calculate_daily_penalty(inst, on_date, daily_rate)
        if inc:
            changed += 1
            total += inc
    return {'installments': changed, 'penalty_total': money(total)}
