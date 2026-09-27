import calendar
import json
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_admin
from app.core.config import settings
from app.db.session import get_db
from app.models import AgreementInstallment, CollectionAgreement, Contribution, Group, LedgerEntry, Loan, LoanInstallment, Member, Payment, PaymentSettlement, User, WebhookEvent
from app.services.mercado_pago import MercadoPagoClient, ProviderCreateAmbiguity
from app.services.notifications_v12 import create_notification
from app.services.payment_settlement import contribution_financial_status, installment_financial_status, settle_confirmed_pix_payment
from app.services.webhook import validate_mercado_pago_signature
from app.services.loan_installment_pix_attempts import approve_or_reconcile, RECONCILIATION_REQUIRED
from app.services.contribution_pix_attempts import (
    bind_provider as bind_contribution_provider,
    lock_contribution,
    mark_ambiguous as mark_contribution_ambiguous,
    mark_reconciliation_required as mark_contribution_reconciliation_required,
    preserve_provider_result_for_reconciliation as preserve_contribution_provider_result,
    reserve as reserve_contribution_payment,
)


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
    if contribution.cancelled_at is not None:
        return ZERO
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
    member, _ = _member_contribution(user, contribution_id, db)
    contribution = lock_contribution(db, contribution_id)
    if contribution is None or contribution.member_id != member.id:
        raise HTTPException(404, "Contribuição não encontrada.")
    financial_status = _contribution_status(contribution)
    if financial_status == "PAID":
        raise HTTPException(409, "Contribuição já paga.")
    if financial_status == "CANCELLED":
        raise HTTPException(409, "Contribuição encerrada neste ciclo.")
    existing = _payment_for_contribution(db, contribution)
    if existing and existing.provider_payment_id:
        if existing.status in {"pending", "in_process", "PENDING"}:
            return _pix_response(existing)
        if existing.status not in {"cancelled", "rejected", "refunded", "charged_back"}:
            return _pix_response(existing)
    if existing and not existing.provider_payment_id:
        # A local placeholder is not a ready-to-use PIX charge. Only the
        # request that committed it may make its first provider call.
        raise HTTPException(409, "Criação Pix pendente de reconciliação.")

    amount_due = _contribution_open_amount(contribution)
    if amount_due <= ZERO:
        raise HTTPException(409, "Contribuição já paga.")

    try:
        payment, created = reserve_contribution_payment(db, contribution.id, amount_due)
    except IntegrityError:
        db.rollback()
        existing = _payment_for_contribution(db, contribution)
        if existing is not None and existing.provider_payment_id:
            return _pix_response(existing)
        raise HTTPException(409, "Criação Pix pendente de reconciliação.")

    if not created:
        if payment.provider_payment_id:
            return _pix_response(payment)
        raise HTTPException(409, "Criação Pix pendente de reconciliação.")

    try:
        result = await MercadoPagoClient().create_pix_payment(
            amount=payment.amount,
            email=user.email,
            cpf=user.cpf,
            description=f"FRcaixinha contribuição {contribution.competence.isoformat()}",
            idempotency_key=payment.idempotency_key,
            external_reference=payment.external_reference,
        )
    except ProviderCreateAmbiguity:
        mark_contribution_ambiguous(db, payment.id)
        raise HTTPException(502, "Não foi possível confirmar a criação do Pix.")
    except Exception:
        # Known HTTP errors such as 4xx have no approved retry contract. Keep
        # the key and local reservation blocked for explicit reconciliation.
        mark_contribution_reconciliation_required(db, payment.id)
        raise HTTPException(502, "Não foi possível confirmar a criação do Pix.")

    try:
        payment = bind_contribution_provider(db, payment.id, contribution.id, result)
    except Exception:
        db.rollback()
        payment = preserve_contribution_provider_result(
            db, payment.id, contribution.id, result
        )
        if (
            payment.attempt_status is None
            and payment.provider_order_id == str(result.get("order_id"))
            and payment.provider_payment_id == str(result.get("id"))
            and payment.reconciliation_status is None
        ):
            return _pix_response(payment, result)
        raise HTTPException(502, "Não foi possível confirmar a criação do Pix.")
    return _pix_response(payment, result)


@router.get("/{payment_id}/receipt")
def payment_receipt(payment_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    payment = db.get(Payment, payment_id)
    is_admin = False
    if user.role == "ADMIN":
        require_admin(user)
        is_admin = True
    member = db.query(Member).filter(Member.user_id == user.id, Member.status == "ACTIVE").first()
    if payment is None or (not is_admin and (member is None or _payment_owner_member_id(db, payment) != member.id)):
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
    if not isinstance(data, dict):
        raise HTTPException(400, "Payload do webhook inválido.")
    body_resource = data.get("data") if isinstance(data.get("data"), dict) else {}
    data_id = str(request.query_params.get("data.id") or body_resource.get("id") or "")
    event_type = str(data.get("type") or "").lower()
    valid = validate_mercado_pago_signature(
        request.headers.get("x-signature"), request.headers.get("x-request-id"), data_id,
        settings.mercado_pago_webhook_secret, max_age_seconds=settings.webhook_signature_max_age_seconds,
    )
    if not valid:
        raise HTTPException(401, "Assinatura do webhook inválida.")
    event_id = str(data.get("id") or f"{event_type}:{data_id}")
    event = db.query(WebhookEvent).filter(
        WebhookEvent.provider == "mercado_pago",
        WebhookEvent.event_id == event_id,
    ).one_or_none()
    if event is None:
        event = WebhookEvent(
            provider="mercado_pago",
            event_id=event_id,
            event_type=event_type or None,
            resource_id=data_id or None,
            processed=False,
        )
        db.add(event)
        try:
            # The authenticated identity must be durable before provider I/O.
            db.commit()
        except IntegrityError:
            db.rollback()
            event = db.query(WebhookEvent).filter(
                WebhookEvent.provider == "mercado_pago",
                WebhookEvent.event_id == event_id,
            ).one_or_none()
            if event is None:
                raise

    stored_type = str(event.event_type or "").lower()
    if event.resource_id and data_id and event.resource_id != data_id:
        db.rollback()
        return {"received": True, "reconciliable": True, "inconsistent": True}
    if stored_type and event_type and stored_type != event_type:
        db.rollback()
        return {"received": True, "reconciliable": True, "inconsistent": True}
    if event.resource_id and not data_id:
        # A repeated event without its signed resource identity cannot trigger
        # processing of the previously stored resource.
        db.rollback()
        return {"received": True, "reconciliable": not event.processed}
    if event.resource_id is None and data_id:
        event.resource_id = data_id
    if event.event_type is None and event_type:
        event.event_type = event_type
    db.commit()

    if event.processed:
        return {"received": True, "duplicate": True}
    outcome = await _process_mercado_pago_event(db, event.id)
    if outcome in {"provider_error", "missing_order"}:
        message = (
            "Pagamento sem provider_order_id para consulta no Mercado Pago."
            if outcome == "missing_order"
            else "Não foi possível consultar o pagamento no Mercado Pago."
        )
        raise HTTPException(502, message)
    return {
        "received": True,
        "reconciliable": outcome not in {"processed", "ignored"},
    }


async def _process_mercado_pago_event(db: Session, event_id: int) -> str:
    """Process one persisted webhook through the canonical payment path."""
    event_query = db.query(WebhookEvent).filter(
        WebhookEvent.id == event_id,
        WebhookEvent.provider == "mercado_pago",
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        event_query = event_query.with_for_update().populate_existing()
    event = event_query.one_or_none()
    if event is None:
        return "missing_event"
    if event.processed:
        return "processed"

    event_type = str(event.event_type or "").lower()
    resource_id = str(event.resource_id or "").strip()
    if event_type not in {"payment", "order"}:
        event.processed = True
        db.commit()
        return "ignored"
    if not resource_id:
        return "missing_resource"

    payment_query = db.query(Payment).filter(Payment.provider == "mercado_pago")
    if event_type == "order":
        matches = payment_query.filter(Payment.provider_order_id == resource_id).all()
    else:
        matches = payment_query.filter(Payment.provider_payment_id == resource_id).all()
    if not matches:
        return "orphan"
    if len(matches) != 1:
        return "ambiguous_payment"

    payment = matches[0]
    if not payment.provider_order_id:
        return "missing_order"
    try:
        remote = await MercadoPagoClient().get_order(payment.provider_order_id)
    except Exception:
        db.rollback()
        return "provider_error"
    if not isinstance(remote, dict):
        db.rollback()
        return "provider_error"

    transactions = remote.get("transactions") or {}
    if not isinstance(transactions, dict):
        db.rollback()
        return "provider_error"
    remote_payments = transactions.get("payments") or []
    if not isinstance(remote_payments, list) or any(
        not isinstance(item, dict) for item in remote_payments
    ):
        db.rollback()
        return "provider_error"
    remote_payment = next(
        (
            item for item in remote_payments
            if str(item.get("id")) == str(payment.provider_payment_id)
        ),
        None,
    ) or {}
    remote_payload = dict(remote)
    remote_payload.update(remote_payment)
    status = remote_payment.get("status") or remote.get("status")
    payment.status = status or payment.status
    payment.raw_status = status or payment.raw_status
    if status in {"refunded", "charged_back"}:
        # Preserve provider evidence; reversals remain explicit only.
        payment.reconciliation_status = RECONCILIATION_REQUIRED

    confirmed = status == "approved" or (
        status == "processed" and remote_payment.get("status_detail") == "accredited"
    )
    if confirmed:
        received = _remote_amount(remote_payment, remote)
        if received is not None:
            payment.amount_received = received
        was_settled = db.query(PaymentSettlement).filter(
            PaymentSettlement.payment_id == payment.id
        ).first() is not None
        if (payment.reference_type or "").upper() == "LOAN_INSTALLMENT":
            # Keep the provider evidence durable before the financial writer.
            db.commit()
            approve_or_reconcile(
                db,
                payment,
                _remote_confirmed_at(remote_payment),
                remote_payload,
                settle_confirmed_pix_payment,
            )
        elif (
            (payment.reference_type or "").upper() == "CONTRIBUTION"
            or _payment_contribution(db, payment) is not None
        ):
            contribution = _payment_contribution(db, payment)
            if contribution is not None and contribution.cancelled_at is not None and not was_settled:
                payment.reconciliation_status = RECONCILIATION_REQUIRED
                db.commit()
                return "reconciliable"
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
                            create_notification(
                                db, member.user_id, "PAYMENT_CONFIRMED", "Pix confirmado",
                                f"Sua contribuição de {contribution.competence.isoformat()} foi confirmada.",
                                "CONTRIBUTION", str(contribution.id),
                            )
                    elif settlement.obligation_type == "LOAN_INSTALLMENT":
                        installment = db.get(LoanInstallment, settlement.loan_installment_id)
                        loan = db.get(Loan, installment.loan_id) if installment is not None else None
                        member = db.get(Member, settlement.member_id)
                        if installment is not None and loan is not None and member is not None:
                            create_notification(
                                db, member.user_id, "LOAN_INSTALLMENT_PAID", "Parcela paga",
                                f"A parcela {installment.number} do empréstimo #{loan.id} foi confirmada.",
                                "LOAN_INSTALLMENT", str(installment.id),
                            )
        elif (payment.reference_type or "").upper() == "AGREEMENT_INSTALLMENT" and payment.reference_id:
            legacy = payment.ledger_posted_at is not None or db.query(LedgerEntry).filter(
                LedgerEntry.reference_type == "AGREEMENT_INSTALLMENT_PAYMENT",
                LedgerEntry.reference_id == str(payment.id),
            ).first() is not None
            if not legacy:
                was_settled = db.query(PaymentSettlement).filter(
                    PaymentSettlement.payment_id == payment.id
                ).first() is not None
                settlement = settle_confirmed_pix_payment(
                    db,
                    payment,
                    confirmation_source="WEBHOOK",
                    webhook_event_id=event.id,
                    remote_payload=remote_payload,
                    confirmed_at=_remote_confirmed_at(remote_payment),
                )
                if not was_settled and settlement.amount_applied > ZERO:
                    installment = db.get(AgreementInstallment, settlement.agreement_installment_id)
                    agreement = db.get(CollectionAgreement, installment.agreement_id) if installment is not None else None
                    member = db.get(Member, settlement.member_id)
                    if installment is not None and agreement is not None and member is not None:
                        create_notification(
                            db, member.user_id, "AGREEMENT_INSTALLMENT_PAID", "Parcela do acordo paga",
                            f"A parcela {installment.number} do acordo #{agreement.id} foi confirmada.",
                            "AGREEMENT_INSTALLMENT", str(installment.id),
                        )

    event.processed = True
    db.commit()
    return "processed"


@router.post("/webhook/mercado-pago/replay")
async def replay_mercado_pago_webhooks(
    event_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=20),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin-only replay of already persisted Mercado Pago webhook evidence."""
    from sqlalchemy import func

    query = db.query(WebhookEvent).filter(
        WebhookEvent.provider == "mercado_pago",
        WebhookEvent.processed.is_(False),
        func.lower(WebhookEvent.event_type).in_(("payment", "order")),
        WebhookEvent.resource_id.is_not(None),
        WebhookEvent.resource_id != "",
    )
    if event_id is not None:
        query = query.filter(WebhookEvent.event_id == event_id)
    events = query.order_by(WebhookEvent.created_at, WebhookEvent.id).limit(limit).all()
    counts = {"selected": len(events), "processed": 0, "orphaned": 0, "unresolved": 0}
    for event in events:
        try:
            outcome = await _process_mercado_pago_event(db, event.id)
        except Exception:
            db.rollback()
            outcome = "processing_error"
        if outcome in {"processed", "ignored"}:
            counts["processed"] += 1
        elif outcome == "orphan":
            counts["orphaned"] += 1
        else:
            counts["unresolved"] += 1
    return counts
