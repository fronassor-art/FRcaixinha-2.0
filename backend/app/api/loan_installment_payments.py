from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.api.deps import current_user
from app.db.session import get_db
from app.models import User, Member, Loan, LoanInstallment, Payment
from app.services.mercado_pago import MercadoPagoClient
from app.services.loan_engine_v17 import installment_due as remaining

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
    tx = (result.get('point_of_interaction') or {}).get('transaction_data') or {}
    return {'payment_id': payment.id, 'provider_payment_id': payment.provider_payment_id,
            'status': payment.status, 'amount': str(payment.amount),
            'qr_code': tx.get('qr_code'), 'qr_code_base64': tx.get('qr_code_base64'),
            'ticket_url': tx.get('ticket_url')}

@router.post('/{installment_id}/pix')
async def create_installment_pix(installment_id: int, user: User=Depends(current_user), db: Session=Depends(get_db)):
    loan, inst = _owned_installment(user, installment_id, db)
    due = remaining(inst)
    if due <= 0 or inst.status == 'PAID':
        raise HTTPException(409, 'Parcela já está paga.')
    ref_type, ref_id = 'LOAN_INSTALLMENT', str(inst.id)
    pending = db.query(Payment).filter(Payment.reference_type == ref_type, Payment.reference_id == ref_id,
                                       Payment.status.in_(['pending','in_process','PENDING'])).order_by(Payment.id.desc()).first()
    if pending:
        return _response(pending)
    idem = f'frc-loan-installment-{inst.id}'
    client = MercadoPagoClient()
    try:
        result = await client.create_pix_payment(amount=due, email=user.email, cpf=user.cpf,
            description=f'FRcaixinha parcela {inst.number} empréstimo {loan.id}',
            idempotency_key=idem, external_reference=f'loan_installment:{inst.id}')
    except Exception as exc:
        raise HTTPException(502, f'Não foi possível criar o Pix: {exc}')
    payment = Payment(provider='mercado_pago', provider_payment_id=str(result['id']), idempotency_key=idem,
                      amount=due, status=result.get('status','PENDING'), raw_status=result.get('status'),
                      reference_type=ref_type, reference_id=ref_id)
    db.add(payment)
    try:
        db.commit(); db.refresh(payment)
    except IntegrityError:
        db.rollback()
        existing = db.query(Payment).filter(Payment.reference_type == ref_type, Payment.reference_id == ref_id,
                                            Payment.status.in_(['pending','in_process','PENDING'])).first()
        if existing: return _response(existing)
        raise HTTPException(409, 'Pagamento já registrado.')
    return _response(payment, result)

@router.get('/{installment_id}/payment')
def installment_payment(installment_id: int, user: User=Depends(current_user), db: Session=Depends(get_db)):
    _, inst = _owned_installment(user, installment_id, db)
    payment = db.query(Payment).filter(Payment.reference_type == 'LOAN_INSTALLMENT', Payment.reference_id == str(inst.id)).order_by(Payment.id.desc()).first()
    return {'payment': None if not payment else _response(payment), 'installment_status': inst.status,
            'paid_amount': str(inst.paid_amount or 0), 'remaining_amount': str(remaining(inst))}
