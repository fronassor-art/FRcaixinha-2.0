import asyncio
import hashlib
import hmac
import json
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import contributions as contributions_api
from app.api import payments as payments_api
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import Contribution, Group, LedgerEntry, Loan, LoanInstallment, Member, Payment, PaymentSettlement, User
from app.schemas.finance import ContributionIn
from app.services.late_charge_v1 import financial_civil_date
from app.services.payment_settlement import settle_confirmed_pix_payment


engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SessionLocal = sessionmaker(bind=engine)
Base.metadata.create_all(engine)


def override_get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _seed(db, suffix):
    group = Group(name=f"Grupo endpoint {suffix}", due_day=15, max_installments=6)
    user = User(name=f"Membro {suffix}", email=f"endpoint-{suffix}@test", cpf=f"cpf-endpoint-{suffix}", password_hash="x", is_active=True)
    db.add_all([group, user])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id, status="ACTIVE")
    db.add(member)
    db.flush()
    return user, member


def _payment(db, suffix, amount, reference_type=None, reference_id=None, order_id=None):
    row = Payment(
        provider="mercado_pago",
        provider_order_id=order_id or f"order-{suffix}",
        provider_payment_id=f"payment-{suffix}",
        idempotency_key=f"idem-{suffix}",
        amount=Decimal(amount),
        status="pending",
        raw_status="pending",
        reference_type=reference_type,
        reference_id=reference_id,
    )
    db.add(row)
    db.flush()
    return row


def _headers(data_id, secret):
    request_id = f"request-{data_id}"
    timestamp = str(int(time.time()))
    manifest = f"id:{data_id};request-id:{request_id};ts:{timestamp};"
    signature = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return {"x-signature": f"ts={timestamp},v1={signature}", "x-request-id": request_id}


def test_new_contribution_has_due_date_and_pix_canonical_reference(monkeypatch):
    db = SessionLocal()
    user, member = _seed(db, "canonical")
    created = contributions_api.create_contribution(ContributionIn(competence=date(2026, 2, 1), amount=Decimal("120.00")), user, db)
    contribution = db.get(Contribution, created["id"])

    async def fake_create(self, **kwargs):
        assert kwargs["amount"] == Decimal("120.00")
        return {"id": "provider-canonical", "order_id": "order-canonical", "status": "pending", "qr_code": "qr", "qr_code_base64": "base64", "ticket_url": "ticket"}

    monkeypatch.setattr(payments_api.MercadoPagoClient, "create_pix_payment", fake_create)
    response = asyncio.run(payments_api.create_pix(contribution.id, user, db))
    payment = db.get(Payment, response["payment_id"])

    assert contribution.due_date == date(2026, 2, 15)
    assert contribution.paid_amount == Decimal("0.00")
    assert payment.reference_type == "CONTRIBUTION"
    assert payment.reference_id == str(contribution.id)
    assert payment.external_reference == f"contribution-{contribution.id}"
    assert contribution.payment_id == payment.id
    db.close()


def test_webhook_contribution_uses_settlement_persists_provider_metadata_and_is_idempotent(monkeypatch):
    db = SessionLocal()
    _, member = _seed(db, "webhook-contribution")
    contribution = Contribution(member_id=member.id, competence=date(2026, 3, 1), amount=Decimal("100.00"), due_date=date.today() + timedelta(days=1), paid_amount=Decimal("0.00"), status="PENDING")
    db.add(contribution)
    db.flush()
    payment = _payment(db, "webhook-contribution", "100.00", "CONTRIBUTION", str(contribution.id))
    payment_id = payment.id
    contribution_id = contribution.id
    db.commit()
    db.close()

    async def fake_order(self, order_id):
        return {
            "id": order_id,
            "external_reference": f"contribution-{contribution_id}",
            "transactions": {"payments": [{
                "id": "payment-webhook-contribution",
                "status": "approved",
                "status_detail": "accredited",
                "transaction_amount": "100.00",
                "date_approved": "2026-03-15T12:00:00Z",
                "point_of_interaction": {"transaction_data": {"txid": "TX-1", "end_to_end_id": "E2E-1"}},
                "access_token": "never-store",
            }]},
        }

    monkeypatch.setattr(payments_api.MercadoPagoClient, "get_order", fake_order)
    old_secret = settings.mercado_pago_webhook_secret
    old_override = app.dependency_overrides.get(get_db)
    secret = "c" * 64
    settings.mercado_pago_webhook_secret = secret
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        first = client.post("/api/payments/webhook/mercado-pago", json={"id": "event-webhook-contribution", "type": "payment", "data": {"id": "payment-webhook-contribution"}}, headers=_headers("payment-webhook-contribution", secret))
        repeated_confirmation = client.post("/api/payments/webhook/mercado-pago", json={"id": "event-webhook-contribution-2", "type": "payment", "data": {"id": "payment-webhook-contribution"}}, headers=_headers("payment-webhook-contribution", secret))
        duplicate_event = client.post("/api/payments/webhook/mercado-pago", json={"id": "event-webhook-contribution", "type": "payment", "data": {"id": "payment-webhook-contribution"}}, headers=_headers("payment-webhook-contribution", secret))
        assert first.status_code == 200
        assert repeated_confirmation.status_code == 200
        assert duplicate_event.json() == {"received": True, "duplicate": True}
    finally:
        settings.mercado_pago_webhook_secret = old_secret
        if old_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = old_override

    db = SessionLocal()
    payment = db.get(Payment, payment_id)
    settlement = db.query(PaymentSettlement).filter(PaymentSettlement.payment_id == payment_id).one()
    assert settlement.obligation_type == "CONTRIBUTION"
    assert db.query(PaymentSettlement).filter(PaymentSettlement.payment_id == payment_id).count() == 1
    assert db.query(LedgerEntry).filter(LedgerEntry.reference_id == str(payment_id)).count() == 1
    assert payment.external_reference == f"contribution-{contribution_id}"
    assert payment.pix_txid == "TX-1"
    assert payment.end_to_end_id == "E2E-1"
    assert payment.provider_status_detail == "accredited"
    assert payment.confirmed_at is not None
    assert payment.amount_received == Decimal("100.00")
    assert "never-store" not in payment.provider_payload_json
    db.close()


def test_receipt_is_authorized_immutable_and_unavailable_before_settlement():
    db = SessionLocal()
    user, member = _seed(db, "receipt-owner")
    other_user, _ = _seed(db, "receipt-other")
    admin_user, _ = _seed(db, "receipt-admin")
    admin_user.role = "ADMIN"
    contribution = Contribution(member_id=member.id, competence=date(2026, 4, 1), amount=Decimal("50.00"), paid_amount=Decimal("0.00"), status="PENDING")
    db.add(contribution)
    db.flush()
    pending = _payment(db, "receipt-pending", "50.00", "CONTRIBUTION", str(contribution.id))
    db.commit()
    with pytest.raises(HTTPException) as unavailable:
        payments_api.payment_receipt(pending.id, user, db)
    assert unavailable.value.status_code == 404

    settled = settle_confirmed_pix_payment(db, pending, confirmation_source="TEST")
    db.commit()
    response = payments_api.payment_receipt(pending.id, user, db)
    assert response == json.loads(settled.receipt_snapshot_json)
    assert payments_api.payment_receipt(pending.id, admin_user, db) == json.loads(settled.receipt_snapshot_json)
    with pytest.raises(HTTPException) as forbidden:
        payments_api.payment_receipt(pending.id, other_user, db)
    assert forbidden.value.status_code == 404
    db.close()


def test_contribution_payment_status_exposes_all_official_states():
    db = SessionLocal()
    user, member = _seed(db, "states")
    financial_today = financial_civil_date(datetime.now(timezone.utc))
    rows = [
        Contribution(member_id=member.id, competence=date(2026, 5, 1), amount=Decimal("10"), due_date=financial_today + timedelta(days=1), paid_amount=Decimal("0"), status="PENDING"),
        Contribution(member_id=member.id, competence=date(2026, 6, 1), amount=Decimal("10"), due_date=financial_today + timedelta(days=1), paid_amount=Decimal("4"), status="PARTIAL"),
        Contribution(member_id=member.id, competence=date(2026, 7, 1), amount=Decimal("10"), due_date=financial_today - timedelta(days=1), paid_amount=Decimal("0"), status="PENDING"),
        Contribution(member_id=member.id, competence=date(2026, 8, 1), amount=Decimal("10"), due_date=financial_today - timedelta(days=1), paid_amount=Decimal("10"), status="PAID"),
    ]
    db.add_all(rows)
    db.commit()
    statuses = [asyncio.run(payments_api.contribution_payment_status(row.id, user, db))["contribution_status"] for row in rows]
    assert statuses == ["PENDING", "PARTIAL", "OVERDUE", "PAID"]
    db.close()


def test_partial_payment_receipts_are_individually_available_by_payment_id():
    db = SessionLocal()
    user, member = _seed(db, "receipt-partials")
    contribution = Contribution(member_id=member.id, competence=date(2026, 5, 1), amount=Decimal("100.00"), paid_amount=Decimal("0.00"), status="PENDING")
    db.add(contribution)
    db.flush()
    first = _payment(db, "receipt-partial-one", "40.00", "CONTRIBUTION", str(contribution.id))
    first_settlement = settle_confirmed_pix_payment(db, first, confirmation_source="TEST")
    second = _payment(db, "receipt-partial-two", "60.00", "CONTRIBUTION", str(contribution.id))
    second_settlement = settle_confirmed_pix_payment(db, second, confirmation_source="TEST")
    db.commit()

    first_receipt = payments_api.payment_receipt(first.id, user, db)
    second_receipt = payments_api.payment_receipt(second.id, user, db)
    assert first_receipt == json.loads(first_settlement.receipt_snapshot_json)
    assert second_receipt == json.loads(second_settlement.receipt_snapshot_json)
    assert first_receipt["payment"]["id"] == first.id
    assert second_receipt["payment"]["id"] == second.id
    assert first_receipt["amounts"]["received"] == "40.00"
    assert second_receipt["amounts"]["received"] == "60.00"
    db.close()
