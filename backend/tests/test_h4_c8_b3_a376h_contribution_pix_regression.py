import asyncio
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import payments as payments_api
from app.db.base import Base
from app.models import Contribution, Group, Member, MemberFinancialEntry, Payment, PaymentSettlement, User, WebhookEvent


class EventRequest:
    headers = {}

    def __init__(self, event_id, payment_id):
        self.query_params = {"data.id": payment_id}
        self.event_id = event_id
        self.payment_id = payment_id

    async def json(self):
        return {"id": self.event_id, "type": "payment", "data": {"id": self.payment_id}}


def _db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _member_and_contribution(db, suffix, amount="25.00"):
    user = User(name=f"Contribution {suffix}", email=f"contribution-{suffix}@test", cpf=f"cpf-{suffix}", password_hash="x", is_active=True)
    group = Group(name=f"Contribution group {suffix}")
    db.add_all([user, group])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id, status="ACTIVE")
    db.add(member)
    db.flush()
    contribution = Contribution(member_id=member.id, competence=date(2026, 2, 1), amount=Decimal(amount), due_date=date(2026, 2, 15), paid_amount=Decimal("0.00"), status="PENDING")
    db.add(contribution)
    db.commit()
    return user, member, contribution


def _bound_contribution_payment(db, member, contribution, suffix, amount="25.00"):
    payment = Payment(provider="mercado_pago", provider_order_id=f"order-{suffix}", provider_payment_id=f"provider-{suffix}", idempotency_key=f"key-{suffix}", amount=Decimal(amount), status="pending", raw_status="pending", reference_type="CONTRIBUTION", reference_id=str(contribution.id))
    db.add(payment)
    db.commit()
    contribution.payment_id = payment.id
    db.commit()
    return payment


def _approved_order(provider_id, amount="25.00"):
    return {"id": "order-" + provider_id, "status": "approved", "transactions": {"payments": [{"id": provider_id, "status": "approved", "status_detail": "accredited", "transaction_amount": amount, "date_approved": "2026-02-15T12:00:00Z", "point_of_interaction": {"transaction_data": {"txid": "tx-" + provider_id, "end_to_end_id": "e2e-" + provider_id}}}]}}


def test_contribution_pix_creation_still_works_after_a376h(monkeypatch):
    db = _db()
    user, member, contribution = _member_and_contribution(db, "creation")
    seen = []

    async def create(self, **kwargs):
        seen.append(kwargs)
        return {"id": "provider-creation", "order_id": "order-creation", "status": "pending", "qr_code": "qr", "qr_code_base64": "b64", "ticket_url": "ticket"}

    monkeypatch.setattr(payments_api.MercadoPagoClient, "create_pix_payment", create)
    result = asyncio.run(payments_api.create_pix(contribution.id, user, db))
    payment = db.get(Payment, result["payment_id"])
    assert seen[0]["amount"] == Decimal("25.00")
    assert payment.reference_type == "CONTRIBUTION" and payment.reference_id == str(contribution.id)
    assert payment.provider_payment_id == "provider-creation"
    assert payment.attempt_status is None
    assert payment.calculated_for_date is None
    assert payment.financial_snapshot_json is None
    assert payment.snapshot_hash is None
    assert payment.expires_at is None
    assert result["provider_payment_id"] == payment.provider_payment_id


def test_contribution_pix_does_not_inherit_loan_d_plus_one_financial_expiry(monkeypatch):
    db = _db()
    user, member, contribution = _member_and_contribution(db, "expiry")

    async def create(self, **kwargs):
        return {"id": "provider-expiry", "order_id": "order-expiry", "status": "pending"}

    monkeypatch.setattr(payments_api.MercadoPagoClient, "create_pix_payment", create)
    result = asyncio.run(payments_api.create_pix(contribution.id, user, db))
    payment = db.get(Payment, result["payment_id"])
    assert payment.reference_type == "CONTRIBUTION"
    assert payment.expires_at is None
    assert payment.calculated_for_date is None
    assert payment.attempt_status is None


def test_contribution_approved_webhook_preserves_existing_settlement_flow(monkeypatch):
    db = _db()
    user, member, contribution = _member_and_contribution(db, "approved")
    payment = _bound_contribution_payment(db, member, contribution, "approved")
    monkeypatch.setattr(payments_api, "validate_mercado_pago_signature", lambda *a, **k: True)

    async def order(self, order_id):
        return _approved_order("provider-approved")

    monkeypatch.setattr(payments_api.MercadoPagoClient, "get_order", order)
    result = asyncio.run(payments_api.mercado_pago_webhook(EventRequest("event-contribution-approved", "provider-approved"), db))
    db.refresh(payment)
    db.refresh(contribution)
    assert result["received"] is True
    assert contribution.status == "PAID"
    assert payment.reconciliation_status is None
    assert payment.attempt_status is None
    assert db.query(PaymentSettlement).filter_by(payment_id=payment.id).count() == 1
    assert db.query(WebhookEvent).filter_by(event_id="event-contribution-approved").one().processed is True


def test_contribution_confirmed_payment_preserves_own_balance_credit_rule(monkeypatch):
    db = _db()
    user, member, contribution = _member_and_contribution(db, "balance")
    payment = _bound_contribution_payment(db, member, contribution, "balance")
    monkeypatch.setattr(payments_api, "validate_mercado_pago_signature", lambda *a, **k: True)

    async def order(self, order_id):
        return _approved_order("provider-balance")

    monkeypatch.setattr(payments_api.MercadoPagoClient, "get_order", order)
    asyncio.run(payments_api.mercado_pago_webhook(EventRequest("event-contribution-balance", "provider-balance"), db))
    entries = db.query(MemberFinancialEntry).filter(MemberFinancialEntry.contribution_id == contribution.id).all()
    assert len(entries) == 1
    assert entries[0].entry_type == "CONTRIBUTION" and entries[0].direction == "CREDIT" and entries[0].amount == Decimal("25.00")


def test_contribution_webhook_retry_is_idempotent_after_a376h(monkeypatch):
    db = _db()
    user, member, contribution = _member_and_contribution(db, "retry")
    payment = _bound_contribution_payment(db, member, contribution, "retry")
    monkeypatch.setattr(payments_api, "validate_mercado_pago_signature", lambda *a, **k: True)
    calls = []

    async def order(self, order_id):
        calls.append(order_id)
        return _approved_order("provider-retry")

    monkeypatch.setattr(payments_api.MercadoPagoClient, "get_order", order)
    request = EventRequest("event-contribution-retry", "provider-retry")
    asyncio.run(payments_api.mercado_pago_webhook(request, db))
    first = (db.query(PaymentSettlement).filter_by(payment_id=payment.id).count(), db.query(MemberFinancialEntry).filter(MemberFinancialEntry.contribution_id == contribution.id).count())
    second = asyncio.run(payments_api.mercado_pago_webhook(request, db))
    second_counts = (db.query(PaymentSettlement).filter_by(payment_id=payment.id).count(), db.query(MemberFinancialEntry).filter(MemberFinancialEntry.contribution_id == contribution.id).count())
    assert second == {"received": True, "duplicate": True}
    assert first == second_counts == (1, 1)
    assert db.query(WebhookEvent).filter_by(event_id="event-contribution-retry").count() == 1
    assert calls == ["order-retry"]


def test_contribution_pix_uses_decimal_safe_provider_amount(monkeypatch):
    db = _db()
    user, member, contribution = _member_and_contribution(db, "decimal", "25.10")
    seen = []

    async def create(self, **kwargs):
        seen.append(kwargs["amount"])
        return {"id": "provider-decimal", "order_id": "order-decimal", "status": "pending"}

    monkeypatch.setattr(payments_api.MercadoPagoClient, "create_pix_payment", create)
    asyncio.run(payments_api.create_pix(contribution.id, user, db))
    assert seen == [Decimal("25.10")]


def test_contribution_attempt_null_is_not_legacy_unverified():
    db = _db()
    user, member, contribution = _member_and_contribution(db, "legacy-rule")
    payment = _bound_contribution_payment(db, member, contribution, "legacy-rule")
    assert payment.attempt_status is None
    assert payment.reference_type == "CONTRIBUTION"
    assert payment.reconciliation_status is None
