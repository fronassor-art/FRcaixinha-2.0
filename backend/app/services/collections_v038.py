from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.models import LoanInstallment, Loan, Member, Group, CollectionEvent
from app.services.notifications_v12 import create_notification

DUE_SOON_DAYS = 3
ESCALATION_DAYS = 7

def stage_for(inst, group, today):
    if inst.paid_at is not None or inst.status == 'PAID': return 'PAID'
    days = (today - inst.due_date).days
    if days < 0:
        return 'DUE_SOON' if days >= -DUE_SOON_DAYS else 'NORMAL'
    if days <= int(group.grace_days or 0): return 'DUE_SOON'
    if days < ESCALATION_DAYS: return 'OVERDUE'
    return 'IN_COLLECTION'

def _event_type(stage, days_late):
    if stage == 'DUE_SOON': return 'REMINDER_DUE'
    if stage == 'OVERDUE': return 'OVERDUE'
    if stage == 'IN_COLLECTION': return 'COLLECTION_ESCALATION'
    return None

def _create_event(db, inst, member, stage, today):
    et = _event_type(stage, max(0, (today-inst.due_date).days))
    if not et: return False
    exists = db.query(CollectionEvent).filter_by(installment_id=inst.id, event_type=et, event_date=today).first()
    if exists: return False
    title = {'REMINDER_DUE':'Parcela próxima do vencimento','OVERDUE':'Parcela em atraso','COLLECTION_ESCALATION':'Atenção: cobrança em andamento'}[et]
    days = max(0, (today-inst.due_date).days)
    message = (f'A parcela {inst.number} do empréstimo #{inst.loan_id} vence em {inst.due_date.isoformat()}.' if et=='REMINDER_DUE'
               else f'A parcela {inst.number} do empréstimo #{inst.loan_id} está em atraso há {days} dia(s). Valor em aberto: R$ {(Decimal(inst.amount or 0)-Decimal(inst.paid_amount or 0)+Decimal(inst.penalty_amount or 0)-Decimal(inst.paid_penalty_amount or 0)).quantize(Decimal("0.01"))}.')
    n = create_notification(db, member.user_id, 'COLLECTION_'+et, title, message, 'LOAN_INSTALLMENT', str(inst.id))
    db.add(CollectionEvent(installment_id=inst.id, member_id=member.id, event_type=et, event_date=today, notification_id=n.id))
    inst.last_collection_at = datetime.now(timezone.utc)
    inst.collection_attempts = (inst.collection_attempts or 0) + 1
    return True

def run_collection_cycle(db: Session, today: date | None = None):
    today = today or date.today()
    rows = db.query(LoanInstallment, Loan, Member, Group).join(Loan, Loan.id==LoanInstallment.loan_id).join(Member, Member.id==Loan.member_id).join(Group, Group.id==Member.group_id).filter(LoanInstallment.paid_at.is_(None), LoanInstallment.status!='PAID').all()
    changed=events=0
    for inst, loan, member, group in rows:
        new_stage = stage_for(inst, group, today)
        if inst.collection_stage != new_stage:
            inst.collection_stage = new_stage; changed += 1
        if _create_event(db, inst, member, new_stage, today): events += 1
    return {'installments_scanned':len(rows),'stage_changes':changed,'events_created':events}

def collections_summary(db: Session, today: date | None = None):
    today=today or date.today()
    rows=db.query(LoanInstallment, Loan, Member, Group).join(Loan,Loan.id==LoanInstallment.loan_id).join(Member,Member.id==Loan.member_id).join(Group,Group.id==Member.group_id).filter(LoanInstallment.paid_at.is_(None),LoanInstallment.status.notin_(['PAID','AGREED'])).all()
    aging={'0-7':Decimal('0'),'8-30':Decimal('0'),'31-60':Decimal('0'),'61+':Decimal('0')}
    overdue_count=0; overdue_balance=Decimal('0'); overdue_amount=Decimal('0')
    for inst,loan,member,group in rows:
        days=(today-inst.due_date).days-int(group.grace_days or 0)
        if days>0:
            overdue_count+=1
            bal=(Decimal(inst.amount or 0)-Decimal(inst.paid_amount or 0)+Decimal(inst.penalty_amount or 0)-Decimal(inst.paid_penalty_amount or 0)).quantize(Decimal('0.01'))
            overdue_balance+=bal; overdue_amount+=bal
            key='0-7' if days<=7 else '8-30' if days<=30 else '31-60' if days<=60 else '61+'
            aging[key]+=bal
    return {'as_of':today.isoformat(),'overdue_installments':overdue_count,'overdue_balance':f'{overdue_balance:.2f}','aging':{k:f'{v:.2f}' for k,v in aging.items()}}
