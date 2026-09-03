import json
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy.orm import Session
from app.models import Loan, LoanInstallment, Member, Group, CollectionAgreement, AgreementInstallment, AuditLog
from app.services.loan_engine_v17 import add_months, money
from app.services.notifications_v12 import create_notification

CENT=Decimal('0.01')
def _split(total,n):
    base=(total/Decimal(n)).quantize(CENT,rounding=ROUND_HALF_UP); out=[]; acc=Decimal('0')
    for i in range(1,n+1):
        v=base if i<n else money(total-acc); out.append(v); acc+=v
    return out

def outstanding_for_loan(db, loan):
    rows=db.query(LoanInstallment).filter(LoanInstallment.loan_id==loan.id, LoanInstallment.status!='PAID').all()
    base=sum((money(i.amount)-money(i.paid_amount) for i in rows),Decimal('0'))
    penalty=sum((money(i.penalty_amount)-money(i.paid_penalty_amount) for i in rows),Decimal('0'))
    return money(base), money(penalty), rows

def request_agreement(db:Session, loan_id:int, user_id:int, installments:int, reason:str|None=None):
    loan=db.get(Loan,loan_id)
    if not loan or loan.status not in ('ACTIVE','OVERDUE','IN_COLLECTION'): raise ValueError('Empréstimo não elegível para acordo.')
    member=db.get(Member,loan.member_id)
    if not member: raise ValueError('Participante não encontrado.')
    existing=db.query(CollectionAgreement).filter(CollectionAgreement.loan_id==loan.id, CollectionAgreement.status=='REQUESTED').first()
    if existing: raise ValueError('Já existe uma solicitação de acordo pendente.')
    base,penalty,rows=outstanding_for_loan(db,loan)
    total=money(base+penalty)
    if total<=0: raise ValueError('Não há saldo para renegociar.')
    if installments<1 or installments>24: raise ValueError('O acordo deve ter entre 1 e 24 parcelas.')
    snapshot={'loan_id':loan.id,'captured_at':datetime.now(timezone.utc).isoformat(),'base':str(base),'penalty':str(penalty),'total':str(total), 'installments':[{'id':i.id,'number':i.number,'due_date':i.due_date.isoformat(),'amount':str(i.amount),'paid_amount':str(i.paid_amount or 0),'penalty':str(i.penalty_amount or 0),'paid_penalty':str(i.paid_penalty_amount or 0)} for i in rows]}
    ag=CollectionAgreement(loan_id=loan.id,member_id=member.id,requested_by=user_id,status='REQUESTED',installments=installments,total_amount=total,reason=reason,snapshot=json.dumps(snapshot,sort_keys=True))
    db.add(ag); db.flush(); return ag

def decide_agreement(db:Session, agreement_id:int, admin_id:int, approve:bool, note:str|None=None):
    ag=db.get(CollectionAgreement,agreement_id)
    if not ag or ag.status!='REQUESTED': raise ValueError('Acordo não encontrado ou já decidido.')
    loan=db.get(Loan,ag.loan_id); member=db.get(Member,ag.member_id)
    if not loan or not member: raise ValueError('Dados do acordo inválidos.')
    ag.status='APPROVED' if approve else 'REJECTED'; ag.decided_by=admin_id; ag.decided_at=datetime.now(timezone.utc)
    db.add(AuditLog(actor_user_id=admin_id,action='COLLECTION_AGREEMENT_DECISION',entity_type='COLLECTION_AGREEMENT',entity_id=str(ag.id),details=json.dumps({'approve':approve,'note':note},ensure_ascii=False)))
    if not approve:
        create_notification(db,member.user_id,'AGREEMENT_REJECTED','Acordo não aprovado','Sua solicitação de acordo financeiro não foi aprovada.','COLLECTION_AGREEMENT',str(ag.id)); return ag
    base,penalty,rows=outstanding_for_loan(db,loan); total=money(base+penalty)
    if total<=0 or abs(total-ag.total_amount)>CENT: raise ValueError('O saldo do empréstimo mudou desde a solicitação; solicite um novo acordo.')
    # Snapshot antigo permanece intacto; as parcelas antigas são preservadas e marcadas como AGREED.
    for i in rows: i.status='AGREED'; i.collection_stage='PAID' if getattr(i,'collection_stage',None)=='PAID' else 'NORMAL'
    principal_parts=_split(base,ag.installments); penalty_parts=[penalty]+[Decimal('0')]*(ag.installments-1)
    for n in range(1,ag.installments+1):
        principal=principal_parts[n-1]; pen=penalty_parts[n-1]; amount=money(principal+pen)
        db.add(AgreementInstallment(agreement_id=ag.id,number=n,due_date=add_months(date.today(),n),principal=principal,penalty_amount=pen,amount=amount))
    loan.status='RESTRUCTURED'
    create_notification(db,member.user_id,'AGREEMENT_APPROVED','Acordo aprovado',f'Seu acordo do empréstimo #{loan.id} foi aprovado em {ag.installments} parcela(s).','COLLECTION_AGREEMENT',str(ag.id))
    return ag

def agreement_balance(ai):
    penalty=max(Decimal('0'),money(ai.penalty_amount)-money(ai.paid_penalty_amount)); principal=max(Decimal('0'),money(ai.principal)-money(ai.paid_amount)); return money(penalty+principal)
