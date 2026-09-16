from decimal import Decimal

from app.models import AgreementInstallment, LedgerEntry, Payment
from app.services.payment_settlement import settle_confirmed_pix_payment

def apply_confirmed_agreement_payment(db, payment:Payment, installment:AgreementInstallment):
    """Compatibility facade; the canonical writer is settle_confirmed_pix_payment."""
    if payment.ledger_posted_at is not None or payment.amount_received is None:
        return False
    if payment.reference_type != "AGREEMENT_INSTALLMENT" or installment is None or installment.id is None:
        raise ValueError("Pagamento e parcela de acordo incompatíveis.")
    if str(installment.id) != str(payment.reference_id):
        raise ValueError("Parcela legada diverge da referência do Payment.")
    # These compatibility false-results are decided before delegation and do
    # not touch the financial objects. All writer errors must propagate.
    try:
        received = Decimal(str(payment.amount_received))
    except (TypeError, ValueError):
        received = None
    if received is not None and received <= 0:
        return False
    existing_ledger = db.query(LedgerEntry).filter(
        LedgerEntry.reference_type == "AGREEMENT_INSTALLMENT_PAYMENT",
        LedgerEntry.reference_id == str(payment.id),
    ).first()
    if existing_ledger is not None:
        return False
    remaining = max(Decimal("0"), Decimal(installment.penalty_amount or 0) - Decimal(installment.paid_penalty_amount or 0))
    remaining += max(Decimal("0"), Decimal(installment.principal or 0) - Decimal(installment.paid_amount or 0))
    if remaining <= 0:
        return False
    settle_confirmed_pix_payment(db, payment, confirmation_source="LEGACY_COMPAT")
    return True
