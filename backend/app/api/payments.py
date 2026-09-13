import calendar
import json
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.config import settings
from app.db.session import get_db
from app.models import AgreementInstallment, CollectionAgreement, Contribution, Group, Loan, LoanInstallment, Member, Payment, PaymentSettlement, User, WebhookEvent
from app.services.agreement_payments_v039 import apply_confirmed_agreement_payment
from app.services.mercado_pago import MercadoPagoClient
from app.services.notifications_v12 import create_notification
from app.services.payment_settlement import contribution_financial_status, installment_financial_status, settle_confirmed_pix_payment
from app.services.webhook import validate_mercado_pago_signature


router = APIRouter(prefix="/payments", tags=["payments"])
CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(CENT)


def _member_contribution(user, contribution_id, db):
    member = db.query(Member).filter(Member.user_id == user.id, Member.status == "ACTIVE").first()
    contribution = db.get(Contribution, contribution_id)
    if not member or not contribution or contribution.member_id != member.id:
        raise HTTPException(404, "Contribuição não encontrada.")
    return member, contribution


def _contribution_paid_amount(contribution: Contribution) -> Decimal:
    if contribution.paid_amount is not None:
        return _money(contribution.paid_amount)
    return _money(contribution.amount) if contribution.status == "PAID" else ZERO


def _contribution_status(contribution: Contribution) -> str:
    return contribution_financial_status(contribution, _contribution_paid_amount(contribution), datetime.now(timezone.utc))


def _contribution_open_amount(contribution: Contribution) -> Decimal:
    return max(ZERO, _money(contribution.amount) - _contribution_paid_amount(contribution))


def _payment_for_contribution(db: Session, contribution: Contribution):
    canonical = (
        db.query(Payment)
        .filter(Payment.reference_type == "CONTRIBUTION", Payment.reference_id == str(contribution.id))
        .order_by(Payment.id.desc())
        .first()
    )
    return canonical or (db.get(Payment, contribution.payment_id) if contribution.payment_id else None)


def _payment_contribution(db: Session, payment: Payment):
    if (payment.reference_type or "").upper() == "CONTRIBUTION" and (payment.reference_id or "").isdigit():
        contribution = db.get(Contribution, int(payment.reference_id))
        if contribution is not None:
            return contribution
    return db.query(Contribution).filter(Contribution.payment_id == payment.id).first()


def _payment_installment(db: Session, payment: Payment):
    if (payment.reference_type or "").upper() != "LOAN_INSTALLMENT" or not (payment.reference_id or "").isdigit():
        return None
    return db.get(LoanInstallment, int(payment.reference_id))


def _payment_owner_member_id(db: Session, payment: Payment):
    settlement = db.query(PaymentSettlement).filter(PaymentSettlement.payment_id == payment.id).one_or_none()
    if settlement is not None:
        return settlement.member_id
    contribution = _payment_contribution(db, payment)
    if contribution is not None:
        return contribution.member_id
    installment = _payment_installment(db, payment)
    if installment is not None:
        loan = db.get(Loan, installment.loan_id)
        return loan.member_id if loan is not None else None
    return None


def _pix_response(payment, result=None):
    result = result or {}
    return {
        "payment_id": payment.id,
        "provider_payment_id": payment.provider_payment_id,
        "status": payment.status,
        "amount": str(payment.amount),
        "qr_code": result.get("qr_code") or payment.qr_code,
        "qr_code_base64": result.get("qr_code_base64") or payment.qr_code_base64,
        "ticket_url": result.get("ticket_url") or payment.ticket_url,
    }


def _due_date(competence: date, due_day: int | None) -> date | None:
    if due_day is None:
        return None
    return date(competence.year, competence.month, min(max(1, int(due_day)), calendar.monthrange(competence.year, competence.month)[1]))


def _remote_amount(remote_payment: dict, remote_order: dict) -> Decimal | None:
    details = remote_payment.get("transaction_details") or {}
    for value in (
        remote_payment.get("transaction_amount"),
        remote_payment.get("amount"),
        details.get("total_paid_amount") if isinstance(details, dict) else None,
        remote_order.get("total_amount"),
    ):
        if value is None:
            continue
        try:
            amount = _money(value)
        except (InvalidOperation, ValueError):
            continue
        if amount >= ZERO:
            return amount
    return None


def _remote_confirmed_at(remote_payment: dict) -> datetime | None:
    value = remote_payment.get("date_approved") or remote_payment.get("approved_at")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


@router.post("/pix/{contribution_id}")
async def create_pix(contribution_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _, contribution = _member_contribution(user, contribution_id, db)
    financial_status = _contribution_status(contribution)
    if financial_status == "PAID":
        raise HTTPException(409, "Contribuição já paga.")
    existing = _payment_for_contribution(db, contribution)
    if existing and existing.status in {"pending", "in_process", "PENDING"}:
        return _pix_response(existing)
    if existing and existing.status not in {"cancelled", "rejected", "refunded", "charged_back"}:
        return _pix_response(existing)

    if not contribution.pix_idempotency_key:
        contribution.pix_idempotency_key = f"frc-contribution-{contribution.id}"
        db.flush()
    amount_due = _contribution_open_amount(contribution)
    if amount_due <= ZERO:
        raise HTTPException(409, "Contribuição já paga.")
    idempotency_key = contribution.pix_idempotency_key
    external_reference = f"contribution-{contribution.id}"
    try:
        result = await MercadoPagoClient().create_pix_payment(
            amount=amount_due,
            email=user.email,
            cpf=user.cpf,
            description=f"FRcaixinha contribuição {contribution.competence.isoformat()}",
            idempotency_key=idempotency_key,
            external_reference=external_reference,
        )
    except Exception as exc:
        raise HTTPException(502, f"Não foi possível criar o Pix no Mercado Pago: {exc}")

    payment = Payment(
        provider="mercado_pago",
        provider_order_id=str(result.get("order_id")) if result.get("order_id") else None,
        provider_payment_id=str(result["id"]),
        idempotency_key=idempotency_key,
        amount=amount_due,
        status=result.get("status", "PENDING"),
        raw_status=result.get("status"),
        qr_code=result.get("qr_code"),
        qr_code_base64=result.get("qr_code_base64"),
        ticket_url=result.get("ticket_url"),
        external_reference=external_reference,
        reference_type="CONTRIBUTION",
        reference_id=str(contribution.id),
    )
    db.add(payment)
    try:
        db.flush()
        contribution.payment_id = payment.id
        db.commit()
        db.refresh(payment)
    except IntegrityError:
        db.rollback()
        existing = _payment_for_contribution(db, contribution)
        if existing is not None:
            return _pix_response(existing)
        raise HTTPException(409, "Pagamento já registrado para esta contribuição.")
    return _pix_response(payment, result)


@router.get("/{payment_id}/receipt")
def payment_receipt(payment_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    payment = db.get(Payment, payment_id)
    member = db.query(Member).filter(Member.user_id == user.id, Member.status == "ACTIVE").first()
    if payment is None or member is None or _payment_owner_member_id(db, payment) != member.id:
        raise HTTPException(404, "Recibo não encontrado.")
    settlement = db.query(PaymentSettlement).filter(PaymentSettlement.payment_id == payment.id).one_or_none()
    if settlement is None:
        raise HTTPException(404, "Recibo ainda não disponível.")
    return json.loads(settlement.receipt_snapshot_json)


@router.get("/{payment_id}")
async def payment_status(payment_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    payment = db.get(Payment, payment_id)
    member = db.query(Member).filter(Member.user_id == user.id, Member.status == "ACTIVE").first()
    if payment is None or member is None or _payment_owner_member_id(db, payment) != member.id:
        raise HTTPException(404, "Pagamento não encontrado.")
    settlement = db.query(PaymentSettlement).filter(PaymentSettlement.payment_id == payment.id).one_or_none()
    contribution = _payment_contribution(db, payment)
    installment = _payment_installment(db, payment)
    obligation_status = settlement.obligation_status_after if settlement else None
    if contribution is not None:
        obligation_status = obligation_status or _contribution_status(contribution)
    if installment is not None:
        obligation_status = obligation_status or installment_financial_status(installment, datetime.now(timezone.utc))
    return {
        "payment_id": payment.id,
        "provider_payment_id": payment.provider_payment_id,
        "status": payment.status,
        "obligation_status": obligation_status,
        "contribution_status": _contribution_status(contribution) if contribution is not None else None,
        "installment_status": installment_financial_status(installment, datetime.now(timezone.utc)) if installment is not None else None,
        "amount": str(payment.amount),
    }


@router.get("/contribution/{contribution_id}")
async def contribution_payment_status(contribution_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _, contribution = _member_contribution(user, contribution_id, db)
    payment = _payment_for_contribution(db, contribution)
    return {
        "payment": None if payment is None else _pix_response(payment),
        "contribution_status": _contribution_status(contribution),
        "paid_amount": str(_contribution_paid_amount(contribution)),
        "remaining_amount": str(_contribution_open_amount(contribution)),
    }


@router.post("/webhook/mercado-pago")
async def mercado_pago_webhook(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    data_id = request.query_params.get("data.id") or str((data.get("data") or {}).get("id") or "")
    valid = validate_mercado_pago_signature(
        request.headers.get("x-signature"), request.headers.get("x-request-id"), data_id,
        settings.mercado_pago_webhook_secret, max_age_seconds=settings.webhook_signature_max_age_seconds,
    )
    if not valid:
        raise HTTPException(401, "Assinatura do webhook inválida.")
    event_id = str(data.get("id") or f"{data.get('type')}:{data_id}")
    if db.query(WebhookEvent).filter(WebhookEvent.provider == "mercado_pago", WebhookEvent.event_id == event_id).first():
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
        if payment is not None:
            if not payment.provider_order_id:
                db.rollback()
                raise HTTPException(502, "Pagamento sem provider_order_id para consulta no Mercado Pago.")
            client = MercadoPagoClient()
            try:
                remote = await client.get_order(payment.provider_order_id)
            except Exception:
                db.rollback()
                raise HTTPException(502, "Não foi possível consultar o pagamento no Mercado Pago.")
            remote_payments = ((remote.get("transactions") or {}).get("payments") or [])
            remote_payment = next((item for item in remote_payments if str(item.get("id")) == str(payment.provider_payment_id)), None) or {}
            remote_payload = dict(remote)
            remote_payload.update(remote_payment)
            status = remote_payment.get("status") or remote.get("status")
            payment.status = status or payment.status
            payment.raw_status = status or payment.raw_status
            if status == "approved":
                received = _remote_amount(remote_payment, remote)
                if received is not None:
                    payment.amount_received = received
                was_settled = db.query(PaymentSettlement).filter(PaymentSettlement.payment_id == payment.id).first() is not None
                if (payment.reference_type or "").upper() in {"CONTRIBUTION", "LOAN_INSTALLMENT"} or _payment_contribution(db, payment) is not None:
                    if payment.ledger_posted_at is None or was_settled:
                        settlement = settle_confirmed_pix_payment(
                            db,
                            payment,
                            confirmation_source="WEBHOOK",
                            webhook_event_id=event.id,
                            remote_payload=remote_payload,
                            confirmed_at=_remote_confirmed_at(remote_payment),
                        )
                        if not was_settled:
                            if settlement.obligation_type == "CONTRIBUTION":
                                contribution = db.get(Contribution, settlement.contribution_id)
                                member = db.get(Member, settlement.member_id)
                                if contribution is not None and member is not None:
                                    create_notification(db, member.user_id, "PAYMENT_CONFIRMED", "Pix confirmado", f"Sua contribuição de {contribution.competence.isoformat()} foi confirmada.", "CONTRIBUTION", str(contribution.id))
                            elif settlement.obligation_type == "LOAN_INSTALLMENT":
                                installment = db.get(LoanInstallment, settlement.loan_installment_id)
                                loan = db.get(Loan, installment.loan_id) if installment is not None else None
                                member = db.get(Member, settlement.member_id)
                                if installment is not None and loan is not None and member is not None:
                                    create_notification(db, member.user_id, "LOAN_INSTALLMENT_PAID", "Parcela paga", f"A parcela {installment.number} do empréstimo #{loan.id} foi confirmada.", "LOAN_INSTALLMENT", str(installment.id))
                elif (payment.reference_type or "").upper() == "AGREEMENT_INSTALLMENT" and payment.reference_id:
                    installment = db.get(AgreementInstallment, int(payment.reference_id))
                    if installment is not None:
                        changed = apply_confirmed_agreement_payment(db, payment, installment)
                        agreement = db.get(CollectionAgreement, installment.agreement_id)
                        if changed and agreement is not None:
                            member = db.get(Member, agreement.member_id)
                            if member is not None:
                                create_notification(db, member.user_id, "AGREEMENT_INSTALLMENT_PAID", "Parcela do acordo paga", f"A parcela {installment.number} do acordo #{agreement.id} foi confirmada.", "AGREEMENT_INSTALLMENT", str(installment.id))
            event.processed = True
    else:
        event.processed = True
    db.commit()
    return {"received": True}
