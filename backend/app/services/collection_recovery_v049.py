from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models import CollectionCase, PaymentPromise, LoanInstallment, Loan, Member, AuditLog
from app.services.collections_v038 import collections_summary

def _open_case(db, member_id, loan_id=None, stage='SOFT'):
    q=db.query(CollectionCase).filter(CollectionCase.member_id==member_id, CollectionCase.status=='OPEN')
    if loan_id is not None: q=q.filter(CollectionCase.loan_id==loan_id)
    case=q.first()
    if case: return case, False
    now=datetime.now(timezone.utc)
    case=CollectionCase(member_id=member_id, loan_id=loan_id, stage=stage, opened_at=now, next_action_at=now+timedelta(days=1))
    db.add(case); db.flush(); return case, True

def sync_cases(db: Session, today: date|None=None):
    today=today or date.today(); opened=escalated=resolved=0
    rows=db.query(LoanInstallment,Loan,Member).join(Loan,Loan.id==LoanInstallment.loan_id).join(Member,Member.id==Loan.member_id).filter(LoanInstallment.paid_at.is_(None),LoanInstallment.status.notin_(['PAID','AGREED'])).all()
    for inst,loan,member in rows:
        days=(today-inst.due_date).days
        if days <= 0: continue
        stage='SOFT' if days<=7 else 'INTENSIVE' if days<=30 else 'RECOVERY'
        case,created=_open_case(db,member.id,loan.id,stage)
        opened += int(created)
        if case.stage != stage: case.stage=stage; case.last_action_at=datetime.now(timezone.utc); escalated+=1
        case.next_action_at=datetime.now(timezone.utc)+timedelta(days=1 if days<31 else 3)
    open_cases=db.query(CollectionCase).filter(CollectionCase.status=='OPEN').all()
    for case in open_cases:
        if case.loan_id:
            balance=db.query(func.coalesce(func.sum(LoanInstallment.amount-LoanInstallment.paid_amount+LoanInstallment.penalty_amount-LoanInstallment.paid_penalty_amount),0)).filter(LoanInstallment.loan_id==case.loan_id,LoanInstallment.paid_at.is_(None),LoanInstallment.status!='AGREED').scalar()
            if Decimal(balance or 0) <= 0:
                case.status='RESOLVED'; case.resolved_at=datetime.now(timezone.utc); resolved+=1
    return {'cases_opened':opened,'cases_escalated':escalated,'cases_resolved':resolved}

def create_promise(db, case_id, admin_id, amount, promised_date, note=None):
    case=db.get(CollectionCase,case_id)
    if not case or case.status!='OPEN': raise ValueError('Caso de cobrança não está aberto.')
    if Decimal(amount)<=0: raise ValueError('Valor prometido deve ser positivo.')
    if promised_date < date.today(): raise ValueError('Data prometida não pode estar no passado.')
    promise=PaymentPromise(case_id=case.id,member_id=case.member_id,promised_amount=Decimal(amount).quantize(Decimal('0.01')),promised_date=promised_date,created_by=admin_id,note=note)
    case.last_action_at=datetime.now(timezone.utc); case.next_action_at=datetime.combine(promised_date,datetime.min.time(),tzinfo=timezone.utc)
    db.add(promise); db.add(AuditLog(actor_user_id=admin_id,action='COLLECTION_PROMISE_CREATED',entity_type='COLLECTION_CASE',entity_id=str(case.id),details=f'amount={promise.promised_amount};date={promised_date.isoformat()}'))
    db.flush(); return promise

def collection_recovery_summary(db: Session):
    today=date.today()
    overdue=collections_summary(db,today)
    cases=db.query(CollectionCase).filter(CollectionCase.status=='OPEN').count()
    promises=db.query(PaymentPromise).filter(PaymentPromise.status=='PENDING').count()
    due=db.query(PaymentPromise).filter(PaymentPromise.status=='PENDING',PaymentPromise.promised_date<=today).count()
    return {'as_of':today.isoformat(),'open_cases':cases,'pending_promises':promises,'promises_due_or_late':due,'overdue':overdue}
