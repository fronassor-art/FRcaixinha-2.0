from __future__ import annotations
from decimal import Decimal
from sqlalchemy.orm import Session
from app.models import Group, Loan, AuditLog, Member
from app.services.financial_risk_v047 import assess_loan
from app.services.credit_policy_v037 import evaluate_credit_policy
from app.services.risk_v036 import evaluate_release


def evaluate_loan_pipeline(db: Session, loan: Loan, *, persist_risk: bool = True, include_release: bool = False):
    member = db.get(Member, loan.member_id)
    if not member:
        return {'decision':'BLOCK','financial_risk':{'status':'BLOCKED','score':100,'reasons':['Participante não encontrado.']},'credit_policy':None,'release_limits':None,'errors':['Participante não encontrado.']}
    group = db.get(Group, member.group_id)
    if not group:
        return {'decision':'BLOCK','financial_risk':None,'credit_policy':None,'release_limits':None,'errors':['Grupo do participante não encontrado.']}

    errors=[]
    financial = assess_loan(db, loan, persist=persist_risk)
    policy = evaluate_credit_policy(db, loan, group)
    release = evaluate_release(db, loan, group) if include_release else None

    # v0.48: explicit per-loan limits complement exposure and credit policy.
    if group.max_loan_amount is not None and Decimal(loan.principal) > Decimal(group.max_loan_amount):
        errors.append(f'Valor máximo por empréstimo excedido: R$ {Decimal(loan.principal):.2f} > R$ {Decimal(group.max_loan_amount):.2f}.')
    income = Decimal(member.declared_monthly_income or 0)
    if group.max_loan_income_multiple is not None and income > 0 and Decimal(loan.principal) > income * Decimal(group.max_loan_income_multiple):
        errors.append('Valor do empréstimo excede o múltiplo máximo da renda configurado.')
    if policy['status'] != 'PASS':
        errors.extend(policy.get('errors', []))
    if financial['status'] == 'BLOCKED':
        errors.extend(financial.get('reasons', []))
    if include_release and release and release['status'] != 'PASS':
        errors.extend(release.get('errors', []))

    if financial['status'] == 'BLOCKED' or (include_release and release and release['status'] != 'PASS'):
        decision='BLOCK'
    elif financial['status'] == 'REVIEW' or policy['status'] != 'PASS' or errors:
        decision='REVIEW'
    else:
        decision='ALLOW'
    return {'decision':decision,'financial_risk':financial,'credit_policy':policy,'release_limits':release,
            'limits':{'max_loan_amount':str(group.max_loan_amount) if group.max_loan_amount is not None else None,
                      'max_loan_income_multiple':str(group.max_loan_income_multiple) if group.max_loan_income_multiple is not None else None},
            'errors':list(dict.fromkeys(errors))}


def assert_loan_approval_allowed(db: Session, loan: Loan, admin_id: int, force_exception: bool = False, admin_note: str | None = None):
    result = evaluate_loan_pipeline(db, loan, persist_risk=True, include_release=False)
    if result['decision'] == 'BLOCK' and not force_exception:
        raise ValueError({'code':'FINANCIAL_APPROVAL_BLOCKED','pipeline':result})
    if result['decision'] == 'REVIEW' and not force_exception:
        raise ValueError({'code':'FINANCIAL_APPROVAL_REVIEW','pipeline':result})
    if force_exception:
        if not admin_note:
            raise ValueError('Exceção de aprovação exige justificativa do administrador.')
        db.add(AuditLog(actor_user_id=admin_id, action='FINANCIAL_APPROVAL_EXCEPTION', entity_type='LOAN', entity_id=str(loan.id), details=admin_note))
    return result
