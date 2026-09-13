import asyncio
import hashlib
import hmac
import time
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.loan_installment_payments import create_installment_pix
from app.api import payments as payments_module
from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import (
    Group,
    LedgerEntry,
    Loan,
    LoanInstallment,
    Member,
    Payment,
    PaymentSettlement,
    User,
)
from app.services.ledger import verify_ledger_chain


engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine)
Base.metadata.create_all(engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def seed_installment(db, *, suffix, amount=Decimal("120.00"), penalty=Decimal("0.00")):
    user = User(
        name=f"Membro Pix {suffix}",
        email=f"pix-{suffix}@example.com",
        cpf=f"12345678{suffix:03d}",
        password_hash=hash_password("Teste123!"),
        role="USER",
        is_active=True,
    )
    db.add(user)
    db.flush()
    group = Group(name=f"Grupo Pix {suffix}")
    db.add(group)
    db.flush()
    member = Member(user_id=user.id, group_id=group.id, status="ACTIVE")
    db.add(member)
    db.flush()
    loan = Loan(
        member_id=member.id,
        principal=Decimal("100.00"),
        monthly_rate=Decimal("0.20"),
        installments=1,
        status="ACTIVE",
    )
    db.add(loan)
    db.flush()
    installment = LoanInstallment(
        loan_id=loan.id,
        number=1,
        due_date=date.today(),
        principal=Decimal("100.00"),
        interest=Decimal("20.00"),
        amount=amount,
        penalty_amount=penalty,
        status="OPEN",
    )
    db.add(installment)
    db.commit()
    return user, loan, installment


def test_installment_pix_persists_and_reuses_pending_charge(monkeypatch):
    db = TestingSessionLocal()
    user, _, installment = seed_installment(db, suffix=101)
    calls = []

    async def fake_create(self, **kwargs):
        calls.append(kwargs)
        return {
            "id": "PAY-INSTALLMENT-101",
            "order_id": "ORDER-INSTALLMENT-101",
            "status": "pending",
            "qr_code": "000201PIX-INSTALLMENT-101",
            "qr_code_base64": "base64-101",
            "ticket_url": "https://pix.example/101",
        }

    monkeypatch.setattr(
        "app.api.loan_installment_payments.MercadoPagoClient.create_pix_payment",
        fake_create,
    )

    first = asyncio.run(create_installment_pix(installment.id, user, db))
    payment = db.get(Payment, first["payment_id"])

    assert payment.provider_order_id == "ORDER-INSTALLMENT-101"
    assert payment.provider_payment_id == "PAY-INSTALLMENT-101"
    assert payment.status == "pending"
    assert payment.raw_status == "pending"
    assert payment.qr_code == "000201PIX-INSTALLMENT-101"
    assert payment.qr_code_base64 == "base64-101"
    assert payment.ticket_url == "https://pix.example/101"
    assert payment.reference_type == "LOAN_INSTALLMENT"
    assert payment.reference_id == str(installment.id)

    second = asyncio.run(create_installment_pix(installment.id, user, db))

    assert len(calls) == 1
    assert second["payment_id"] == first["payment_id"]
    assert second["qr_code"] == first["qr_code"]
    assert second["qr_code_base64"] == first["qr_code_base64"]
    assert second["ticket_url"] == first["ticket_url"]
    assert db.query(Payment).count() == 1
    db.close()


def test_webhook_approved_settles_installment_idempotently_and_hashes_ledger(monkeypatch):
    db = TestingSessionLocal()
    user, loan, installment = seed_installment(
        db,
        suffix=102,
        amount=Decimal("120.00"),
        penalty=Decimal("10.00"),
    )
    payment = Payment(
        provider="mercado_pago",
        provider_order_id="ORDER-INSTALLMENT-102",
        provider_payment_id="PAY-INSTALLMENT-102",
        idempotency_key="frc-loan-installment-102",
        amount=Decimal("130.00"),
        status="pending",
        raw_status="pending",
        qr_code="000201PIX-INSTALLMENT-102",
        qr_code_base64="base64-102",
        ticket_url="https://pix.example/102",
        reference_type="LOAN_INSTALLMENT",
        reference_id=str(installment.id),
    )
    db.add(payment)
    db.commit()
    payment_id = payment.id
    installment_id = installment.id
    loan_id = loan.id
    db.close()

    async def fake_get_order(self, order_id):
        assert order_id == "ORDER-INSTALLMENT-102"
        return {
            "id": order_id,
            "status": "approved",
            "transactions": {
                "payments": [
                    {"id": "PAY-INSTALLMENT-102", "status": "approved"},
                ],
            },
        }

    monkeypatch.setattr(payments_module.MercadoPagoClient, "get_order", fake_get_order)
    original_secret = settings.mercado_pago_webhook_secret
    original_db_override = app.dependency_overrides.get(get_db)
    secret = "b" * 64
    settings.mercado_pago_webhook_secret = secret
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        data_id = "PAY-INSTALLMENT-102"
        request_id = "REQUEST-INSTALLMENT-102"
        timestamp = str(int(time.time()))
        manifest = f"id:{data_id};request-id:{request_id};ts:{timestamp};"
        signature = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        payload = {"id": "EVENT-INSTALLMENT-102", "type": "payment", "data": {"id": data_id}}
        headers = {"x-signature": f"ts={timestamp},v1={signature}", "x-request-id": request_id}

        response = client.post("/api/payments/webhook/mercado-pago", json=payload, headers=headers)
        duplicate = client.post("/api/payments/webhook/mercado-pago", json=payload, headers=headers)

        assert response.status_code == 200
        assert duplicate.json() == {"received": True, "duplicate": True}
    finally:
        settings.mercado_pago_webhook_secret = original_secret
        if original_db_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = original_db_override

    db = TestingSessionLocal()
    updated_payment = db.get(Payment, payment_id)
    updated_installment = db.get(LoanInstallment, installment_id)
    updated_loan = db.get(Loan, loan_id)
    entries = db.query(LedgerEntry).filter(
        LedgerEntry.reference_id == str(payment_id),
    ).order_by(LedgerEntry.id).all()

    assert updated_payment.status == "approved"
    assert updated_payment.ledger_posted_at is not None
    assert db.query(PaymentSettlement).filter(PaymentSettlement.payment_id == payment_id).count() == 1
    assert updated_installment.status == "PAID"
    assert updated_installment.paid_amount == Decimal("120.00")
    assert updated_installment.paid_penalty_amount == Decimal("10.00")
    assert updated_loan.status == "PAID"
    assert [(entry.reference_type, entry.amount) for entry in entries] == [
        ("LOAN_PENALTY_PAYMENT", Decimal("10.00")),
        ("LOAN_INTEREST_PAYMENT", Decimal("20.00")),
    ]
    assert all(entry.entry_hash and entry.previous_hash is not None for entry in entries[1:])
    assert entries[0].entry_hash is not None
    assert verify_ledger_chain(db)["status"] == "PASS"
    db.close()
