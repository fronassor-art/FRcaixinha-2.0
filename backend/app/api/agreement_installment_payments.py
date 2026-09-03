from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.api.deps import current_user
from app.db.session import get_db
from app.models import User, Member, CollectionAgreement, AgreementInstallment, Payment
from app.services.mercado_pago import MercadoPagoClient
from app.services.agreements_v039 import agreement_balance

router=APIRouter(prefix='/agreement-installments',tags=['agreement-installments'])

def _owned(user, iid, db):
    ai=db.get(AgreementInstallment,iid)
    if not ai: raise HTTPException(404,'Parcela do acordo não encontrada.')
    ag=db.get(CollectionAgreement,ai.agreement_id); member=db.get(Member,ag.member_id) if ag else None
    if not ag or not member or member.user_id!=user.id or ag.status not in ('APPROVED','SETTLED'): raise HTTPException(404,'Parcela do acordo não encontrada.')
    return ag,ai

@router.post('/{installment_id}/pix')
async def create_pix(installment_id:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    ag,ai=_owned(user,installment_id,db); due=agreement_balance(ai)
    if due<=0 or ai.status=='PAID': raise HTTPException(409,'Parcela já está paga.')
    ref='AGREEMENT_INSTALLMENT'; rid=str(ai.id)
    pending=db.query(Payment).filter(Payment.reference_type==ref,Payment.reference_id==rid,Payment.status.in_(['pending','in_process','PENDING'])).order_by(Payment.id.desc()).first()
    if pending: return {'payment_id':pending.id,'provider_payment_id':pending.provider_payment_id,'status':pending.status,'amount':str(pending.amount)}
    idem=f'frc-agreement-installment-{ai.id}'
    try:
        result=await MercadoPagoClient().create_pix_payment(amount=due,email=user.email,cpf=user.cpf,description=f'FRcaixinha acordo #{ag.id} parcela {ai.number}',idempotency_key=idem,external_reference=f'agreement_installment:{ai.id}')
    except Exception as exc: raise HTTPException(502,f'Não foi possível criar o Pix: {exc}')
    payment=Payment(provider='mercado_pago',provider_payment_id=str(result['id']),idempotency_key=idem,amount=due,status=result.get('status','PENDING'),raw_status=result.get('status'),reference_type=ref,reference_id=rid)
    db.add(payment)
    try: db.commit(); db.refresh(payment)
    except IntegrityError:
        db.rollback(); existing=db.query(Payment).filter(Payment.reference_type==ref,Payment.reference_id==rid,Payment.status.in_(['pending','in_process','PENDING'])).first()
        if existing: return {'payment_id':existing.id,'provider_payment_id':existing.provider_payment_id,'status':existing.status,'amount':str(existing.amount)}
        raise HTTPException(409,'Pagamento já registrado.')
    tx=(result.get('point_of_interaction') or {}).get('transaction_data') or {}
    return {'payment_id':payment.id,'provider_payment_id':payment.provider_payment_id,'status':payment.status,'amount':str(payment.amount),'qr_code':tx.get('qr_code'),'qr_code_base64':tx.get('qr_code_base64'),'ticket_url':tx.get('ticket_url')}

@router.get('/{installment_id}/payment')
def payment(installment_id:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    _,ai=_owned(user,installment_id,db); p=db.query(Payment).filter(Payment.reference_type=='AGREEMENT_INSTALLMENT',Payment.reference_id==str(ai.id)).order_by(Payment.id.desc()).first()
    return {'payment':None if not p else {'payment_id':p.id,'provider_payment_id':p.provider_payment_id,'status':p.status,'amount':str(p.amount)},'installment_status':ai.status,'remaining_amount':str(agreement_balance(ai))}
