from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.api.deps import current_user
from app.core.config import settings
from app.db.session import get_db
from app.models import User, Member, Contribution, Payment, WebhookEvent, LoanInstallment, Loan, AgreementInstallment, CollectionAgreement
from app.services.mercado_pago import MercadoPagoClient
from app.services.webhook import validate_mercado_pago_signature
from app.services.ledger import post_contribution_payment
from app.services.notifications_v12 import create_notification
from app.services.loan_payments_v17 import apply_confirmed_payment
from app.services.agreement_payments_v039 import apply_confirmed_agreement_payment

router = APIRouter(prefix="/payments", tags=["payments"])

def _member_contribution(user, contribution_id, db):
    member = db.query(Member).filter(Member.user_id == user.id, Member.status == "ACTIVE").first()
    contribution = db.get(Contribution, contribution_id)
    if not member or not contribution or contribution.member_id != member.id:
        raise HTTPException(404, "Contribuição não encontrada.")
    return member, contribution

def _pix_response(payment, result=None):
    result = result or {}
    pix = (result.get("point_of_interaction") or {}).get("transaction_data") or {}
    return {
        "payment_id": payment.id,
        "provider_payment_id": payment.provider_payment_id,
        "status": payment.status,
        "amount": str(payment.amount),
        "qr_code": pix.get("qr_code"),
        "qr_code_base64": pix.get("qr_code_base64"),
        "ticket_url": pix.get("ticket_url"),
    }

@router.post("/pix/{contribution_id}")
async def create_pix(contribution_id: int, user: User=Depends(current_user), db: Session=Depends(get_db)):
    member, contribution = _member_contribution(user, contribution_id, db)
    if contribution.status == "PAID":
        raise HTTPException(409, "Contribuição já paga.")
    if contribution.payment_id:
        existing = db.get(Payment, contribution.payment_id)
        if existing and existing.status in {"pending", "in_process", "PENDING"}:
            # A mesma cobrança pendente é reutilizada; não cria outra cobrança.
            result = {"status": existing.status}
            return _pix_response(existing, result)
        if existing and existing.status not in {"cancelled", "rejected", "refunded", "charged_back"}:
            return _pix_response(existing, {"status": existing.status})

    # Chave determinística por recurso: requisições concorrentes/repetidas
    # devem representar a mesma cobrança no provedor e no banco.
    # O Access Token do Mercado Pago permanece exclusivamente no backend.
    idem = f"frc-contribution-{contribution.id}"
    client = MercadoPagoClient()
    try:
        result = await client.create_pix_payment(
            amount=contribution.amount, email=user.email, cpf=user.cpf,
            description=f"FRcaixinha contribuição {contribution.competence.isoformat()}",
            idempotency_key=idem,
            external_reference=f"contribution:{contribution.id}",
        )
    except Exception as exc:
        raise HTTPException(502, f"Não foi possível criar o Pix no Mercado Pago: {exc}")

    payment = Payment(
        provider="mercado_pago", provider_payment_id=str(result["id"]), idempotency_key=idem,
        amount=contribution.amount, status=result.get("status", "PENDING"), raw_status=result.get("status"),
    )
    db.add(payment)
    try:
        db.flush()
        contribution.payment_id = payment.id
        db.commit(); db.refresh(payment)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Pagamento já registrado para esta contribuição.")
    return _pix_response(payment, result)

@router.get("/{payment_id}")
async def payment_status(payment_id: int, user: User=Depends(current_user), db: Session=Depends(get_db)):
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "Pagamento não encontrado.")
    contribution = db.query(Contribution).filter(Contribution.payment_id == payment.id).first()
    member = db.query(Member).filter(Member.user_id == user.id, Member.status == "ACTIVE").first()
    if not member or not contribution or contribution.member_id != member.id:
        raise HTTPException(404, "Pagamento não encontrado.")
    return {
        "payment_id": payment.id,
        "provider_payment_id": payment.provider_payment_id,
        "status": payment.status,
        "contribution_status": contribution.status,
        "amount": str(payment.amount),
    }

@router.get("/contribution/{contribution_id}")
async def contribution_payment_status(contribution_id: int, user: User=Depends(current_user), db: Session=Depends(get_db)):
    _, contribution = _member_contribution(user, contribution_id, db)
    if not contribution.payment_id:
        return {"payment": None, "contribution_status": contribution.status}
    payment = db.get(Payment, contribution.payment_id)
    return {"payment": None if not payment else {
        "payment_id": payment.id, "provider_payment_id": payment.provider_payment_id,
        "status": payment.status, "amount": str(payment.amount),
    }, "contribution_status": contribution.status}

@router.post("/webhook/mercado-pago")
async def mercado_pago_webhook(request: Request, db: Session=Depends(get_db)):
    data = await request.json()
    data_id = request.query_params.get("data.id") or str((data.get("data") or {}).get("id") or "")
    valid = validate_mercado_pago_signature(
        request.headers.get("x-signature"), request.headers.get("x-request-id"),
        data_id, settings.mercado_pago_webhook_secret,
        max_age_seconds=settings.webhook_signature_max_age_seconds,
    )
    if not valid:
        raise HTTPException(401, "Assinatura do webhook inválida.")
    event_id = str(data.get("id") or f"{data.get('type')}:{data_id}")
    existing = db.query(WebhookEvent).filter(WebhookEvent.provider == "mercado_pago", WebhookEvent.event_id == event_id).first()
    if existing:
        return {"received": True, "duplicate": True}
    event = WebhookEvent(provider="mercado_pago", event_id=event_id, event_type=data.get("type"), processed=False)
    db.add(event)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"received": True, "duplicate": True}

    if data.get("type") == "payment" and data_id:
        payment = db.query(Payment).filter(Payment.provider == "mercado_pago", Payment.provider_payment_id == data_id).first()
        if payment:
            client = MercadoPagoClient()
            try:
                remote = await client.get_payment(data_id)
            except Exception:
                db.rollback()
                raise HTTPException(502, "Não foi possível consultar o pagamento no Mercado Pago.")
            status = remote.get("status")
            payment.status = status or payment.status
            payment.raw_status = status
            if status == "approved":
                contribution = db.query(Contribution).filter(Contribution.payment_id == payment.id).first()
                if contribution:
                    contribution.status = "PAID"
                    post_contribution_payment(db, payment)
                    member = db.query(Member).filter(Member.id == contribution.member_id).first()
                    if member:
                        create_notification(db, member.user_id, "PAYMENT_CONFIRMED", "Pix confirmado",
                            f"Sua contribuição de {contribution.competence.isoformat()} foi confirmada.",
                            "CONTRIBUTION", str(contribution.id))
                if payment.reference_type == "LOAN_INSTALLMENT" and payment.reference_id:
                    installment = db.get(LoanInstallment, int(payment.reference_id))
                    if installment:
                        changed = apply_confirmed_payment(db, payment, installment)
                        loan = db.get(Loan, installment.loan_id)
                        if changed and loan:
                            member = db.get(Member, loan.member_id)
                            if member:
                                create_notification(db, member.user_id, "LOAN_INSTALLMENT_PAID", "Parcela paga",
                                    f"A parcela {installment.number} do empréstimo #{loan.id} foi confirmada.",
                                    "LOAN_INSTALLMENT", str(installment.id))
                if payment.reference_type == "AGREEMENT_INSTALLMENT" and payment.reference_id:
                    installment = db.get(AgreementInstallment, int(payment.reference_id))
                    if installment:
                        changed = apply_confirmed_agreement_payment(db, payment, installment)
                        ag = db.get(CollectionAgreement, installment.agreement_id)
                        if changed and ag:
                            member = db.get(Member, ag.member_id)
                            if member:
                                create_notification(db, member.user_id, "AGREEMENT_INSTALLMENT_PAID", "Parcela do acordo paga",
                                    f"A parcela {installment.number} do acordo #{ag.id} foi confirmada.",
                                    "AGREEMENT_INSTALLMENT", str(installment.id))
            event.processed = True
    else:
        event.processed = True
    db.commit()
    return {"received": True}
