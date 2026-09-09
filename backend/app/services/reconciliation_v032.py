from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import func
from app.models import Contribution, Payment, WebhookEvent, LedgerEntry, LoanInstallment

ZERO = Decimal('0.00')

def money(v):
    return str(Decimal(v or 0).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

def reconcile(db):
    findings = []
    def add(code, ok, details, expected=None, observed=None):
        findings.append({'code': code, 'status': 'PASS' if ok else 'FAIL', 'details': details,
                         'expected': money(expected) if expected is not None else None,
                         'observed': money(observed) if observed is not None else None})

    paid_contrib = Decimal(db.query(func.coalesce(func.sum(Contribution.amount), 0)).filter(Contribution.status=='PAID').scalar() or 0)
    contrib_credits = Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount), 0)).filter(
        LedgerEntry.direction=='CREDIT', LedgerEntry.reference_type=='CONTRIBUTION_PAYMENT').scalar() or 0)
    add('CONTRIBUTIONS_LEDGER', paid_contrib == contrib_credits,
        'Contribuições PAID devem corresponder aos créditos de contribuição no Ledger.', paid_contrib, contrib_credits)

    approved = Decimal(db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.status=='approved').scalar() or 0)
    posted = Decimal(db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.status=='approved', Payment.ledger_posted_at.is_not(None)).scalar() or 0)
    add('APPROVED_PAYMENTS_POSTED', approved == posted,
        'Todo pagamento aprovado deve estar marcado como lançado no Ledger.', approved, posted)

    duplicate_provider = db.query(Payment.provider, Payment.provider_payment_id, func.count(Payment.id)).group_by(
        Payment.provider, Payment.provider_payment_id).having(func.count(Payment.id) > 1).all()
    add('DUPLICATE_PROVIDER_PAYMENT', len(duplicate_provider) == 0,
        'Não pode existir mais de um registro para o mesmo pagamento do provedor.')

    duplicate_webhook = db.query(WebhookEvent.provider, WebhookEvent.event_id, func.count(WebhookEvent.id)).group_by(
        WebhookEvent.provider, WebhookEvent.event_id).having(func.count(WebhookEvent.id) > 1).all()
    add('DUPLICATE_WEBHOOK', len(duplicate_webhook) == 0,
        'Eventos Webhook devem ser únicos por provedor/event_id.')

    open_negative = db.query(LoanInstallment).filter(
        LoanInstallment.status != 'PAID',
        (LoanInstallment.amount + LoanInstallment.penalty_amount - LoanInstallment.paid_amount) < 0
    ).count()
    add('NEGATIVE_INSTALLMENT_BALANCE', open_negative == 0,
        'Parcelas abertas não podem apresentar saldo negativo.')

    unprocessed = db.query(WebhookEvent).filter(WebhookEvent.processed == False).count()  # noqa: E712
    add('UNPROCESSED_WEBHOOKS', unprocessed == 0,
        'Eventos Webhook pendentes exigem processamento/reprocessamento antes do fechamento.')

    return {'status': 'PASS' if all(x['status']=='PASS' for x in findings) else 'FAIL', 'findings': findings}
