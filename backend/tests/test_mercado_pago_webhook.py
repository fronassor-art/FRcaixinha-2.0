import hashlib
import hmac
import time
from decimal import Decimal
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.db.base import Base
from app.db.session import get_db
from app.models import User, Group, Member, Contribution, Payment, LedgerEntry
from app.core.security import hash_password


engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSessionLocal = sessionmaker(bind=engine)
Base.metadata.create_all(engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


def test_webhook_approved_baixa_contribuicao_e_ledger():
    db = TestingSessionLocal()

    user = User(
        name="Teste Webhook",
        email="webhook@example.com", cpf="11144477735",
        password_hash=hash_password("Teste123!"), role="USER",
        is_active=True,
    )
    db.add(user)
    db.flush()

    group = Group(name="Grupo Webhook")
    db.add(group)
    db.flush()

    member = Member(
        user_id=user.id,
        group_id=group.id,
        status="ACTIVE",
    )
    db.add(member)
    db.flush()

    payment = Payment(
        provider="mercado_pago",
        provider_order_id="ORDER-TEST-APPROVED",
        provider_payment_id="PAY-TEST-APPROVED",
        idempotency_key="IDEMP-TEST-APPROVED",
        amount=Decimal("150.00"),
        status="processing",
        raw_status="in_process",
    )
    db.add(payment)
    db.flush()

    contribution = Contribution(
        member_id=member.id,
        competence=date(2026, 9, 1),
        amount=Decimal("150.00"),
        status="PENDING",
        payment_id=payment.id,
    )
    db.add(contribution)
    db.flush()
    payment_id = payment.id
    contribution_id = contribution.id
    db.commit()
    db.close()

    async def fake_get_order(self, order_id):
        assert order_id == "ORDER-TEST-APPROVED"
        return {
            "id": "ORDER-TEST-APPROVED",
            "status": "approved",
            "transactions": {
                "payments": [
                    {
                        "id": "PAY-TEST-APPROVED",
                        "status": "approved",
                        "status_detail": "accredited",
                    }
                ]
            },
        }

    from app.api import payments as payments_module
    original = payments_module.MercadoPagoClient.get_order
    payments_module.MercadoPagoClient.get_order = fake_get_order

    try:
        secret = "a" * 64
        data_id = "PAY-TEST-APPROVED"
        request_id = "REQUEST-TEST"
        ts = str(int(time.time()))

        manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
        signature = hmac.new(
            secret.encode(),
            manifest.encode(),
            hashlib.sha256,
        ).hexdigest()

        from app.core.config import settings
        original_secret = settings.mercado_pago_webhook_secret
        settings.mercado_pago_webhook_secret = secret

        response = client.post(
            "/api/payments/webhook/mercado-pago",
            json={
                "id": "EVENT-TEST-APPROVED",
                "type": "payment",
                "data": {"id": data_id},
            },
            headers={
                "x-signature": f"ts={ts},v1={signature}",
                "x-request-id": request_id,
            },
        )

        assert response.status_code == 200
        assert response.json() == {"received": True}

        db = TestingSessionLocal()

        updated_payment = db.get(Payment, payment_id)
        updated_contribution = db.get(Contribution, contribution_id)
        ledger_entries = db.query(LedgerEntry).all()

        assert updated_payment.status == "approved"
        assert updated_contribution.status == "PAID"
        assert len(ledger_entries) == 1
        assert ledger_entries[0].reference_type == "CONTRIBUTION_PAYMENT"
        assert ledger_entries[0].reference_id == str(payment_id)

        db.close()
        settings.mercado_pago_webhook_secret = original_secret

    finally:
        payments_module.MercadoPagoClient.get_order = original
