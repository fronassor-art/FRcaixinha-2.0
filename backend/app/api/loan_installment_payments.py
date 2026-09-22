from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.api.deps import current_user
from app.db.session import get_db
from app.models import User, Member, Loan, LoanInstallment, Payment
from app.services.mercado_pago import MercadoPagoClient
from app.services.loan_engine_v17 import installment_due as remaining
from app.services.loan_installment_pix_attempts import reserve, bind_provider, mark_ambiguous
from app.services.payment_settlement import installment_financial_status

router = APIRouter(prefix='/loan-installments', tags=['loan-installments'])

def _owned_installment(user, installment_id, db):
    member = db.query(Member).filter(Member.user_id == user.id, Member.status == 'ACTIVE').first()
    inst = db.get(LoanInstallment, installment_id)
    if not member or not inst:
        raise HTTPException(404, 'Parcela não encontrada.')
    loan = db.get(Loan, inst.loan_id)
    if not loan or loan.member_id != member.id:
        raise HTTPException(404, 'Parcela não encontrada.')
    return loan, inst

def _response(payment, result=None):
    result = result or {}
    return {
        "payment_id": payment.id,
        "provider_payment_id": payment.provider_payment_id,
        "status": payment.status,
        "amount": str(payment.amount),
        "qr_code": result.get("qr_code") or payment.qr_code,
        "qr_code_base64": result.get("qr_code_base64") or payment.qr_code_base64,
        "ticket_url": result.get("ticket_url") or payment.ticket_url,
        "attempt_status": payment.attempt_status,
        "calculated_for_date": payment.calculated_for_date.isoformat() if payment.calculated_for_date else None,
        "expires_at": payment.expires_at.isoformat() if payment.expires_at else None,
        "reconciliation_status": payment.reconciliation_status,
    }

@router.post('/{installment_id}/pix')
async def create_installment_pix(installment_id: int, user: User=Depends(current_user), db: Session=Depends(get_db)):
    loan, inst = _owned_installment(user, installment_id, db)
    try:
        payment, _created = reserve(db, inst.id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if payment.provider_payment_id:
        return _response(payment)
    try:
        result = await MercadoPagoClient().create_pix_payment(
            amount=payment.amount, email=user.email, cpf=user.cpf,
            description=f'FRcaixinha parcela {inst.number} empréstimo {loan.id}',
            idempotency_key=payment.idempotency_key,
            external_reference=payment.external_reference,
        )
        payment = bind_provider(db, payment.id, result)
    except Exception as exc:
        mark_ambiguous(db, payment.id)
        raise HTTPException(502, f'Não foi possível confirmar a criação do Pix: {exc}')
    return _response(payment, result)

@router.get('/{installment_id}/payment')
def installment_payment(installment_id: int, user: User=Depends(current_user), db: Session=Depends(get_db)):
    _, inst = _owned_installment(user, installment_id, db)
    payment = db.query(Payment).filter(Payment.reference_type == 'LOAN_INSTALLMENT', Payment.reference_id == str(inst.id)).order_by(Payment.id.desc()).first()
    return {'payment': None if not payment else _response(payment), 'installment_status': installment_financial_status(inst, datetime.now(timezone.utc)),
            'paid_amount': str(inst.paid_amount or 0), 'remaining_amount': str(remaining(inst))}
