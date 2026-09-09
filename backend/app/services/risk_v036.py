from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models import Group, Loan, LedgerEntry, AgreementInstallment, CollectionAgreement

CENT = Decimal('0.01')

def money(v):
    return Decimal(v or 0).quantize(CENT, rounding=ROUND_HALF_UP)

def cash_balance(db: Session) -> Decimal:
    credits = Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount), 0)).filter(LedgerEntry.direction == 'CREDIT').scalar() or 0)
    debits = Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount), 0)).filter(LedgerEntry.direction == 'DEBIT').scalar() or 0)
    return money(credits - debits)

def exposure(db: Session, member_id: int | None = None) -> Decimal:
    q = db.query(func.coalesce(func.sum(Loan.principal), 0)).filter(Loan.status == 'ACTIVE')
    if member_id is not None:
        q = q.filter(Loan.member_id == member_id)
    total = money(q.scalar() or 0)
    aq = db.query(func.coalesce(func.sum(AgreementInstallment.principal + AgreementInstallment.penalty_amount - AgreementInstallment.paid_amount - AgreementInstallment.paid_penalty_amount), 0)).join(CollectionAgreement, CollectionAgreement.id == AgreementInstallment.agreement_id).filter(CollectionAgreement.status == 'APPROVED', AgreementInstallment.status != 'PAID')
    if member_id is not None:
        aq = aq.filter(CollectionAgreement.member_id == member_id)
    return money(total + Decimal(aq.scalar() or 0))

def evaluate_release(db: Session, loan: Loan, group: Group):
    balance = cash_balance(db)
    global_before = exposure(db)
    member_before = exposure(db, loan.member_id)
    global_after = money(global_before + loan.principal)
    member_after = money(member_before + loan.principal)
    reserve = money(group.min_cash_reserve)
    available_after = money(balance - loan.principal)
    errors = []
    if available_after < reserve:
        errors.append(f'Saldo mínimo de segurança não pode ser violado: após liberação ficariam R$ {available_after:.2f}, mínimo R$ {reserve:.2f}.')
    if group.max_member_exposure is not None and member_after > money(group.max_member_exposure):
        errors.append(f'Limite de exposição do participante excedido: R$ {member_after:.2f} > R$ {money(group.max_member_exposure):.2f}.')
    if group.max_global_exposure is not None and global_after > money(group.max_global_exposure):
        errors.append(f'Limite global de exposição excedido: R$ {global_after:.2f} > R$ {money(group.max_global_exposure):.2f}.')
    if group.max_exposure_ratio is not None:
        ratio = Decimal(group.max_exposure_ratio)
        if balance <= 0:
            if global_after > 0:
                errors.append('Limite de exposição sobre o caixa: caixa disponível é zero.')
        elif global_after / balance > ratio:
            errors.append(f'Razão máxima de exposição excedida: {(global_after / balance * 100):.2f}% > {(ratio * 100):.2f}%.')
    return {
        'status': 'PASS' if not errors else 'BLOCKED',
        'errors': errors,
        'cash_balance': money(balance),
        'minimum_cash_reserve': reserve,
        'cash_after_release': available_after,
        'global_exposure_before': global_before,
        'global_exposure_after': global_after,
        'member_exposure_before': member_before,
        'member_exposure_after': member_after,
        'limits': {
            'max_member_exposure': money(group.max_member_exposure) if group.max_member_exposure is not None else None,
            'max_global_exposure': money(group.max_global_exposure) if group.max_global_exposure is not None else None,
            'max_exposure_ratio': str(group.max_exposure_ratio) if group.max_exposure_ratio is not None else None,
        }
    }
