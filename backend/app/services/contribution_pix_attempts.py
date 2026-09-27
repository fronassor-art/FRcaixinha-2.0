"""Durable local reservation for Contribution PIX creation.

The Contribution row is locked before checking for a pending Payment. The
reservation transaction commits before the provider request; an ambiguous
provider result remains pending and is not posted again automatically.
"""

from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from app.models import Contribution, Payment


PENDING = "PENDING"
PROVIDER_CREATE_UNKNOWN = "PROVIDER_CREATE_UNKNOWN"
RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


def _is_postgresql(db) -> bool:
    return db.bind is not None and db.bind.dialect.name == "postgresql"


def lock_contribution(db, contribution_id: int) -> Contribution | None:
    query = db.query(Contribution).filter(Contribution.id == contribution_id)
    if _is_postgresql(db):
        query = query.with_for_update().populate_existing()
    return query.one_or_none()


def _pending_payment(db, contribution_id: int) -> Payment | None:
    query = (
        db.query(Payment)
        .filter(
            Payment.reference_type == "CONTRIBUTION",
            Payment.reference_id == str(contribution_id),
            Payment.attempt_status == PENDING,
        )
        .order_by(Payment.id.desc())
    )
    if _is_postgresql(db):
        query = query.with_for_update()
    return query.first()


def reserve(db, contribution_id: int, amount: Decimal) -> tuple[Payment, bool]:
    """Return the durable PENDING attempt and whether this call created it."""
    contribution = lock_contribution(db, contribution_id)
    if contribution is None:
        raise ValueError("Contribution not found")

    existing = _pending_payment(db, contribution.id)
    if existing is not None:
        return existing, False

    idempotency_key = contribution.pix_idempotency_key
    if not idempotency_key:
        idempotency_key = f"frc-contribution-{contribution.id}"
        contribution.pix_idempotency_key = idempotency_key

    payment = Payment(
        provider="mercado_pago",
        provider_order_id=None,
        provider_payment_id=None,
        idempotency_key=idempotency_key,
        amount=amount,
        status="PENDING",
        raw_status="PENDING",
        external_reference=f"contribution-{contribution.id}",
        reference_type="CONTRIBUTION",
        reference_id=str(contribution.id),
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
        winner = _pending_payment(db, contribution_id)
        if winner is None:
            raise
        return winner, False


def bind_provider(
    db,
    payment_id: int,
    contribution_id: int,
    result: dict,
) -> Payment:
    payment = db.get(Payment, payment_id)
    contribution = db.get(Contribution, contribution_id)
    if (
        payment is None
        or contribution is None
        or payment.reference_type != "CONTRIBUTION"
        or payment.reference_id != str(contribution_id)
        or payment.attempt_status != PENDING
    ):
        raise ValueError("Contribution PIX reservation is unavailable")

    provider_payment_id = result.get("id")
    provider_order_id = result.get("order_id")
    if not provider_payment_id or not provider_order_id:
        raise ValueError("Provider response cannot bind the PIX reservation")

    payment.provider_order_id = str(provider_order_id)
    payment.provider_payment_id = str(provider_payment_id)
    payment.status = result.get("status") or "PENDING"
    payment.raw_status = payment.status
    payment.qr_code = result.get("qr_code")
    payment.qr_code_base64 = result.get("qr_code_base64")
    payment.ticket_url = result.get("ticket_url")
    payment.attempt_status = None
    payment.reconciliation_status = None
    contribution.payment_id = payment.id

    db.commit()
    db.refresh(payment)
    return payment


def preserve_provider_result_for_reconciliation(
    db,
    payment_id: int,
    contribution_id: int,
    result: dict,
) -> Payment:
    """Persist known provider identity without completing a failed local bind."""
    provider_payment_id = result.get("id") if isinstance(result, dict) else None
    provider_order_id = result.get("order_id") if isinstance(result, dict) else None
    if not provider_payment_id or not provider_order_id:
        raise ValueError("Provider response lacks order or payment identity")

    payment = db.get(Payment, payment_id)
    contribution = db.get(Contribution, contribution_id)
    if (
        payment is None
        or contribution is None
        or payment.provider != "mercado_pago"
        or payment.reference_type != "CONTRIBUTION"
        or payment.reference_id != str(contribution_id)
    ):
        raise ValueError("Contribution PIX reservation is unavailable")

    expected_order_id = str(provider_order_id)
    expected_payment_id = str(provider_payment_id)
    if payment.provider_order_id not in (None, expected_order_id):
        raise ValueError("Contribution PIX reservation has conflicting provider order identity")
    if payment.provider_payment_id not in (None, expected_payment_id):
        raise ValueError("Contribution PIX reservation has conflicting provider payment identity")

    # A commit may have succeeded before bind_provider raised during refresh.
    # Keep that completed bind exactly as persisted; never downgrade it.
    if (
        payment.attempt_status is None
        and payment.provider_order_id == expected_order_id
        and payment.provider_payment_id == expected_payment_id
    ):
        return payment

    if payment.attempt_status != PENDING:
        raise ValueError("Contribution PIX reservation is no longer pending")

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
