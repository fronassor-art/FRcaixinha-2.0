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

def _response(payment):
    return {
        'payment_id': payment.id,
        'provider_payment_id': payment.provider_payment_id,
        'status': payment.status,
        'amount': str(payment.amount),
        'qr_code': payment.qr_code,
        'qr_code_base64': payment.qr_code_base64,
        'ticket_url': payment.ticket_url,
    }

@router.post('/{installment_id}/pix')
async def create_pix(installment_id:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    ag,ai=_owned(user,installment_id,db); due=agreement_balance(ai)
    if due<=0 or ai.status=='PAID': raise HTTPException(409,'Parcela já está paga.')
    ref='AGREEMENT_INSTALLMENT'; rid=str(ai.id)
    pending=db.query(Payment).filter(Payment.reference_type==ref,Payment.reference_id==rid,Payment.status.in_(['pending','in_process','PENDING'])).order_by(Payment.id.desc()).first()
    if pending: return _response(pending)
    idem=f'frc-agreement-installment-{ai.id}'
    external_reference=f'agreement_installment:{ai.id}'
    try:
        result=await MercadoPagoClient().create_pix_payment(amount=due,email=user.email,cpf=user.cpf,description=f'FRcaixinha acordo #{ag.id} parcela {ai.number}',idempotency_key=idem,external_reference=external_reference)
    except Exception as exc: raise HTTPException(502,f'Não foi possível criar o Pix: {exc}')
    payment=Payment(
        provider='mercado_pago',
        provider_order_id=str(result.get('order_id')) if result.get('order_id') else None,
        provider_payment_id=str(result['id']),
        idempotency_key=idem,
        amount=due,
        status=result.get('status','PENDING'),
        raw_status=result.get('status'),
        qr_code=result.get('qr_code'),
        qr_code_base64=result.get('qr_code_base64'),
        ticket_url=result.get('ticket_url'),
        external_reference=external_reference,
        reference_type=ref,
        reference_id=rid,
    )
    db.add(payment)
    try: db.commit(); db.refresh(payment)
    except IntegrityError:
        db.rollback(); existing=db.query(Payment).filter(Payment.reference_type==ref,Payment.reference_id==rid,Payment.status.in_(['pending','in_process','PENDING'])).first()
        if existing: return _response(existing)
        raise HTTPException(409,'Pagamento já registrado.')
    return _response(payment)

@router.get('/{installment_id}/payment')
def payment(installment_id:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    _,ai=_owned(user,installment_id,db); p=db.query(Payment).filter(Payment.reference_type=='AGREEMENT_INSTALLMENT',Payment.reference_id==str(ai.id)).order_by(Payment.id.desc()).first()
    return {'payment':None if not p else _response(p),'installment_status':ai.status,'remaining_amount':str(agreement_balance(ai))}
