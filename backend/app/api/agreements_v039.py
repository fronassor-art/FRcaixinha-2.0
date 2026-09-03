from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api.deps import current_user, require_admin
from app.db.session import get_db
from app.models import User, Member, Loan, CollectionAgreement, AgreementInstallment
from app.services.agreements_v039 import request_agreement, decide_agreement, agreement_balance
from pydantic import BaseModel, Field

router=APIRouter(tags=['collection-agreements'])
class AgreementRequest(BaseModel):
    loan_id:int
    installments:int=Field(ge=1,le=24)
    reason:str|None=None
class AgreementDecision(BaseModel):
    approve:bool
    note:str|None=None

@router.post('/collections/agreements')
def create(data:AgreementRequest,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member=db.query(Member).filter(Member.user_id==user.id,Member.status=='ACTIVE').first()
    if not member: raise HTTPException(403,'Somente membro ativo.')
    loan=db.get(Loan,data.loan_id)
    if not loan or loan.member_id!=member.id: raise HTTPException(404,'Empréstimo não encontrado.')
    try: ag=request_agreement(db,data.loan_id,user.id,data.installments,data.reason); db.commit(); db.refresh(ag)
    except ValueError as e: db.rollback(); raise HTTPException(409,str(e))
    return {'id':ag.id,'status':ag.status,'loan_id':ag.loan_id,'total_amount':str(ag.total_amount),'installments':ag.installments}

@router.get('/collections/agreements')
def mine(user:User=Depends(current_user),db:Session=Depends(get_db)):
    member=db.query(Member).filter(Member.user_id==user.id).first()
    if not member: raise HTTPException(403,'Participante não encontrado.')
    rows=db.query(CollectionAgreement).filter(CollectionAgreement.member_id==member.id).order_by(CollectionAgreement.id.desc()).all()
    return [{'id':a.id,'loan_id':a.loan_id,'status':a.status,'total_amount':str(a.total_amount),'installments':a.installments,'requested_at':a.requested_at.isoformat(),'decided_at':a.decided_at.isoformat() if a.decided_at else None} for a in rows]

@router.get('/admin/collections/agreements')
def admin_list(status:str|None=None,admin=Depends(require_admin),db:Session=Depends(get_db)):
    q=db.query(CollectionAgreement).order_by(CollectionAgreement.id.desc())
    if status: q=q.filter(CollectionAgreement.status==status.upper())
    return [{'id':a.id,'loan_id':a.loan_id,'member_id':a.member_id,'status':a.status,'total_amount':str(a.total_amount),'installments':a.installments,'reason':a.reason,'requested_at':a.requested_at.isoformat(),'decided_at':a.decided_at.isoformat() if a.decided_at else None} for a in q.limit(500).all()]

@router.post('/admin/collections/agreements/{agreement_id}/decision')
def decision(agreement_id:int,data:AgreementDecision,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: ag=decide_agreement(db,agreement_id,admin.id,data.approve,data.note); db.commit()
    except ValueError as e: db.rollback(); raise HTTPException(409,str(e))
    items=db.query(AgreementInstallment).filter(AgreementInstallment.agreement_id==ag.id).order_by(AgreementInstallment.number).all()
    return {'id':ag.id,'status':ag.status,'loan_id':ag.loan_id,'installments':[{'id':i.id,'number':i.number,'due_date':i.due_date.isoformat(),'amount':str(i.amount),'remaining':str(agreement_balance(i)),'status':i.status} for i in items]}
