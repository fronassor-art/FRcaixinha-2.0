"""Durable provider-create reservations for agreement installment PIX."""

from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from app.models import AgreementInstallment, CollectionAgreement, Payment
from app.services.agreements_v039 import lock_collection_agreement


PENDING = "PENDING"
PROVIDER_CREATE_UNKNOWN = "PROVIDER_CREATE_UNKNOWN"
RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


def _is_postgresql(db) -> bool:
    return db.bind is not None and db.bind.dialect.name == "postgresql"


def lock_installment(
    db, installment_id: int
) -> tuple[CollectionAgreement | None, AgreementInstallment | None]:
    """Lock Agreement then Installment, matching the settlement lock order."""
    with db.no_autoflush:
        locator = (
            db.query(AgreementInstallment)
            .filter(AgreementInstallment.id == installment_id)
            .one_or_none()
        )
    if locator is None:
        return None, None

    try:
        agreement = lock_collection_agreement(db, locator.agreement_id)
    except ValueError:
        return None, None

    with db.no_autoflush:
        query = db.query(AgreementInstallment).filter(
            AgreementInstallment.id == installment_id
        )
        if _is_postgresql(db):
            query = query.with_for_update().populate_existing()
        installment = query.one_or_none()
    return agreement, installment


def _pending_attempt(db, installment_id: int) -> Payment | None:
    query = (
        db.query(Payment)
        .filter(
            Payment.reference_type == "AGREEMENT_INSTALLMENT",
            Payment.reference_id == str(installment_id),
            Payment.attempt_status == PENDING,
        )
        .order_by(Payment.id.desc())
    )
    return query.first()


def reserve(
    db, installment: AgreementInstallment, amount: Decimal
) -> tuple[Payment, bool]:
    """Commit one local attempt before the caller contacts the provider.

    The caller must hold the Agreement and AgreementInstallment locks returned
    by ``lock_installment``. The partial unique index elects a winner on
    databases without row-level locks as well.
    """
    existing = _pending_attempt(db, installment.id)
    if existing is not None:
        return existing, False

    idempotency_key = f"frc-agreement-installment-{installment.id}"
    existing = (
        db.query(Payment)
        .filter(Payment.idempotency_key == idempotency_key)
        .one_or_none()
    )
    if existing is not None:
        return existing, False

    payment = Payment(
        provider="mercado_pago",
        provider_order_id=None,
        provider_payment_id=None,
        idempotency_key=idempotency_key,
        amount=amount,
        status="PENDING",
        raw_status="PENDING",
        external_reference=f"agreement_installment:{installment.id}",
        reference_type="AGREEMENT_INSTALLMENT",
        reference_id=str(installment.id),
        attempt_status=PENDING,
        reconciliation_status=None,
    )
    db.add(payment)
    try:
        db.commit()
        db.refresh(payment)
        return payment, True
    except IntegrityError:
        db.rollback()
        winner = _pending_attempt(db, installment.id)
        if winner is None:
            winner = (
                db.query(Payment)
                .filter(Payment.idempotency_key == idempotency_key)
                .one_or_none()
            )
        if winner is None:
            raise
        return winner, False


def bind_provider(
    db, payment_id: int, installment_id: int, result: dict
) -> Payment:
    payment = db.get(Payment, payment_id)
    installment = db.get(AgreementInstallment, installment_id)
    if (
        payment is None
        or installment is None
        or payment.provider != "mercado_pago"
        or payment.reference_type != "AGREEMENT_INSTALLMENT"
        or payment.reference_id != str(installment_id)
        or payment.attempt_status != PENDING
    ):
        raise ValueError("Agreement PIX reservation is unavailable")

    provider_payment_id = result.get("id") if isinstance(result, dict) else None
    provider_order_id = result.get("order_id") if isinstance(result, dict) else None
    if not provider_payment_id or not provider_order_id:
        raise ValueError("Provider response cannot bind the agreement PIX reservation")
    if payment.provider_payment_id not in (None, str(provider_payment_id)):
        raise ValueError("Agreement PIX reservation has conflicting provider payment identity")
    if payment.provider_order_id not in (None, str(provider_order_id)):
        raise ValueError("Agreement PIX reservation has conflicting provider order identity")

    payment.provider_order_id = str(provider_order_id)
    payment.provider_payment_id = str(provider_payment_id)
    payment.status = result.get("status") or "PENDING"
    payment.raw_status = payment.status
    payment.qr_code = result.get("qr_code")
    payment.qr_code_base64 = result.get("qr_code_base64")
    payment.ticket_url = result.get("ticket_url")
    payment.attempt_status = None
    payment.reconciliation_status = None
    db.commit()
    db.refresh(payment)
    return payment


def preserve_provider_result_for_reconciliation(
    db, payment_id: int, installment_id: int, result: dict
) -> Payment:
    """Persist known provider identity without completing a failed bind."""
    provider_payment_id = result.get("id") if isinstance(result, dict) else None
    provider_order_id = result.get("order_id") if isinstance(result, dict) else None
    if not provider_payment_id or not provider_order_id:
        raise ValueError("Provider response lacks order or payment identity")

    payment = db.get(Payment, payment_id)
    installment = db.get(AgreementInstallment, installment_id)
    if (
        payment is None
        or installment is None
        or payment.provider != "mercado_pago"
        or payment.reference_type != "AGREEMENT_INSTALLMENT"
        or payment.reference_id != str(installment_id)
    ):
        raise ValueError("Agreement PIX reservation is unavailable")

    expected_order_id = str(provider_order_id)
    expected_payment_id = str(provider_payment_id)
    if payment.provider_order_id not in (None, expected_order_id):
        raise ValueError("Agreement PIX reservation has conflicting provider order identity")
    if payment.provider_payment_id not in (None, expected_payment_id):
        raise ValueError("Agreement PIX reservation has conflicting provider payment identity")

    # The bind may have committed before a later refresh failed. Never
    # downgrade that already durable result into a reconciliation placeholder.
    if (
        payment.attempt_status is None
        and payment.provider_order_id == expected_order_id
        and payment.provider_payment_id == expected_payment_id
    ):
        return payment
    if payment.attempt_status != PENDING:
        raise ValueError("Agreement PIX reservation is no longer pending")

    payment.provider_order_id = expected_order_id
    payment.provider_payment_id = expected_payment_id
    payment.status = result.get("status") or "PENDING"
    payment.raw_status = payment.status
    payment.qr_code = result.get("qr_code")
    payment.qr_code_base64 = result.get("qr_code_base64")
    payment.ticket_url = result.get("ticket_url")
    payment.attempt_status = PENDING
    payment.reconciliation_status = RECONCILIATION_REQUIRED
    db.commit()
    return payment


def mark_ambiguous(db, payment_id: int) -> Payment | None:
    payment = db.get(Payment, payment_id)
    if payment is not None and payment.attempt_status == PENDING:
        payment.reconciliation_status = PROVIDER_CREATE_UNKNOWN
        db.commit()
        db.refresh(payment)
    return payment


def mark_reconciliation_required(db, payment_id: int) -> Payment | None:
    payment = db.get(Payment, payment_id)
    if payment is not None and payment.attempt_status == PENDING:
        payment.reconciliation_status = RECONCILIATION_REQUIRED
        db.commit()
        db.refresh(payment)
    return payment
