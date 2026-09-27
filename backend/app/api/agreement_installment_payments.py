from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.api.deps import current_user
from app.db.session import get_db
from app.models import User, Member, CollectionAgreement, AgreementInstallment, Payment
from app.services.mercado_pago import MercadoPagoClient, ProviderCreateAmbiguity
from app.services.agreements_v039 import agreement_balance
from app.services import agreement_installment_pix_attempts as pix_attempts

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
    ag, ai = pix_attempts.lock_installment(db, installment_id)
    member = db.get(Member, ag.member_id) if ag else None
    if (
        not ag
        or not ai
        or not member
        or member.id != ag.member_id
        or ag.status not in ('APPROVED', 'SETTLED')
    ):
        raise HTTPException(404, 'Parcela do acordo não encontrada.')

    due=agreement_balance(ai)
    if due<=0 or ai.status=='PAID': raise HTTPException(409,'Parcela já está paga.')
    ref='AGREEMENT_INSTALLMENT'; rid=str(ai.id)

    pending_attempt = (
        db.query(Payment)
        .filter(
            Payment.reference_type == ref,
            Payment.reference_id == rid,
            Payment.attempt_status == pix_attempts.PENDING,
        )
        .order_by(Payment.id.desc())
        .first()
    )
    usable_statuses = {'pending', 'in_process', 'PENDING'}
    if pending_attempt is not None:
        if (
            pending_attempt.provider_payment_id
            and pending_attempt.status in usable_statuses
        ):
            return _response(pending_attempt)
        raise HTTPException(409, 'Criação Pix pendente de reconciliação.')

    # Preserve reuse of older provider-backed pending agreement charges.
    provider_backed_pending = (
        db.query(Payment)
        .filter(
            Payment.reference_type == ref,
            Payment.reference_id == rid,
            Payment.provider_payment_id.is_not(None),
            Payment.status.in_(usable_statuses),
        )
        .order_by(Payment.id.desc())
        .first()
    )
    if provider_backed_pending is not None:
        return _response(provider_backed_pending)

    try:
        payment, created = pix_attempts.reserve(db, ai, due)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Criação Pix pendente de reconciliação.')
    if not created:
        if payment.provider_payment_id and payment.status in usable_statuses:
            return _response(payment)
        raise HTTPException(409, 'Criação Pix pendente de reconciliação.')

    try:
        result = await MercadoPagoClient().create_pix_payment(
            amount=payment.amount,
            email=user.email,
            cpf=user.cpf,
            description=f'FRcaixinha acordo #{ag.id} parcela {ai.number}',
            idempotency_key=payment.idempotency_key,
            external_reference=payment.external_reference,
        )
    except ProviderCreateAmbiguity:
        pix_attempts.mark_ambiguous(db, payment.id)
        raise HTTPException(502, 'Não foi possível confirmar a criação do Pix.')
    except Exception:
        pix_attempts.mark_reconciliation_required(db, payment.id)
        raise HTTPException(502, 'Não foi possível confirmar a criação do Pix.')

    try:
        payment = pix_attempts.bind_provider(db, payment.id, ai.id, result)
    except Exception:
        db.rollback()
        payment = pix_attempts.preserve_provider_result_for_reconciliation(
            db, payment.id, ai.id, result
        )
        if (
            payment.attempt_status is None
            and payment.provider_order_id == str(result.get('order_id'))
            and payment.provider_payment_id == str(result.get('id'))
            and payment.reconciliation_status is None
        ):
            return _response(payment)
        raise HTTPException(502, 'Não foi possível confirmar a criação do Pix.')
    return _response(payment)

@router.get('/{installment_id}/payment')
def payment(installment_id:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    _,ai=_owned(user,installment_id,db); p=db.query(Payment).filter(Payment.reference_type=='AGREEMENT_INSTALLMENT',Payment.reference_id==str(ai.id)).order_by(Payment.id.desc()).first()
    return {'payment':None if not p else _response(p),'installment_status':ai.status,'remaining_amount':str(agreement_balance(ai))}
