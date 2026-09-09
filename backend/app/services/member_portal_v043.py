from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy.orm import Session
from app.models import User, Member, Contribution, Loan, LoanInstallment, CollectionAgreement, AgreementInstallment

ZERO = Decimal('0.00')

def money(v):
    return str(Decimal(v or 0).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

def _member(db: Session, user_id: int):
    m = db.query(Member).filter(Member.user_id == user_id).first()
    if not m:
        return None, None
    return m, db.get(User, m.user_id)

def portal_dashboard(db: Session, user_id: int):
    member, user = _member(db, user_id)
    if not member or not user:
        return None
    contributions = db.query(Contribution).filter(Contribution.member_id == member.id).order_by(Contribution.competence.desc()).all()
    loans = db.query(Loan).filter(Loan.member_id == member.id).order_by(Loan.id.desc()).all()
    loan_ids = [l.id for l in loans]
    installments = db.query(LoanInstallment).filter(LoanInstallment.loan_id.in_(loan_ids)).order_by(LoanInstallment.due_date).all() if loan_ids else []
    agreements = db.query(CollectionAgreement).filter(CollectionAgreement.member_id == member.id).order_by(CollectionAgreement.id.desc()).all()
    agreement_ids = [a.id for a in agreements]
    agreement_installments = db.query(AgreementInstallment).filter(AgreementInstallment.agreement_id.in_(agreement_ids)).order_by(AgreementInstallment.due_date).all() if agreement_ids else []

    today = date.today()
    contrib_paid = sum((Decimal(c.amount) for c in contributions if c.status == 'PAID'), ZERO)
    loan_paid = sum((Decimal(i.paid_amount or 0) + Decimal(i.paid_penalty_amount or 0) for i in installments), ZERO)
    loan_outstanding = sum((max(ZERO, Decimal(i.amount or 0) - Decimal(i.paid_amount or 0) + Decimal(i.penalty_amount or 0) - Decimal(i.paid_penalty_amount or 0)) for i in installments if i.status != 'PAID'), ZERO)
    overdue = [i for i in installments if i.status != 'PAID' and i.due_date < today]
    overdue_balance = sum((max(ZERO, Decimal(i.amount or 0) - Decimal(i.paid_amount or 0) + Decimal(i.penalty_amount or 0) - Decimal(i.paid_penalty_amount or 0)) for i in overdue), ZERO)
    paid_inst = [i for i in installments if i.status == 'PAID']
    ontime = sum(1 for i in paid_inst if i.paid_at and i.paid_at.date() <= i.due_date)

    return {
        'schema': 'v0.43',
        'privacy': {'scope': 'OWN_DATA_ONLY'},
        'member': {'id': member.id, 'name': user.name, 'status': member.status, 'group_id': member.group_id},
        'summary': {
            'contributions_paid': money(contrib_paid),
            'loan_payments': money(loan_paid),
            'loan_outstanding': money(loan_outstanding),
            'overdue_balance': money(overdue_balance),
            'overdue_installments': len(overdue),
            'on_time_ratio': round(ontime / len(paid_inst), 4) if paid_inst else None,
        },
        'contributions': [
            {'id': c.id, 'competence': c.competence.isoformat(), 'amount': money(c.amount), 'status': c.status}
            for c in contributions[:24]
        ],
        'loans': [
            {'id': l.id, 'principal': money(l.principal), 'installments': l.installments, 'status': l.status}
            for l in loans[:20]
        ],
        'installments': [
            {'id': i.id, 'loan_id': i.loan_id, 'number': i.number, 'due_date': i.due_date.isoformat(),
             'amount': money(i.amount), 'penalty_amount': money(i.penalty_amount),
             'paid_amount': money(i.paid_amount), 'paid_penalty_amount': money(i.paid_penalty_amount),
             'outstanding': money(max(ZERO, Decimal(i.amount or 0)-Decimal(i.paid_amount or 0)+Decimal(i.penalty_amount or 0)-Decimal(i.paid_penalty_amount or 0))),
             'status': i.status, 'collection_stage': i.collection_stage}
            for i in installments[:50]
        ],
        'agreements': [
            {'id': a.id, 'loan_id': a.loan_id, 'status': a.status, 'total_amount': money(a.total_amount), 'installments': a.installments,
             'requested_at': a.requested_at.isoformat(), 'decided_at': a.decided_at.isoformat() if a.decided_at else None}
            for a in agreements[:20]
        ],
        'agreement_installments': [
            {'id': i.id, 'agreement_id': i.agreement_id, 'number': i.number, 'due_date': i.due_date.isoformat(),
             'amount': money(i.amount), 'paid_amount': money(i.paid_amount), 'status': i.status}
            for i in agreement_installments[:50]
        ],
    }
