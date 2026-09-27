"""F12E durable AgreementInstallment PIX reservation contract tests."""

import asyncio
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import agreement_installment_payments as agreement_pix_api
from app.api import payments as payments_api
from app.db.base import Base
from app.models import (
    AgreementInstallment,
    CollectionAgreement,
    Group,
    LedgerEntry,
    Loan,
    Member,
    Payment,
    PaymentReversal,
    PaymentSettlement,
    User,
    WebhookEvent,
)
from app.services import agreement_installment_pix_attempts as pix_attempts
from app.services.mercado_pago import ProviderCreateAmbiguity


class _WebhookRequest:
    headers = {}

    def __init__(self, event_type, event_id, resource_id):
        self.query_params = {"data.id": resource_id}
        self.event_type = event_type
        self.event_id = event_id
        self.resource_id = resource_id

    async def json(self):
        return {
            "id": self.event_id,
            "type": self.event_type,
            "data": {"id": self.resource_id},
        }


def _database(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'agreement-pix.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _seed(db, suffix=None):
    suffix = suffix or uuid.uuid4().hex
    user = User(
        name=f"Agreement {suffix}",
        email=f"agreement-{suffix}@test.invalid",
        cpf=f"cpf-{suffix}",
        password_hash="synthetic-password-hash",
        is_active=True,
    )
    group = Group(name=f"Agreement group {suffix}")
    db.add_all([user, group])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id, status="ACTIVE")
    db.add(member)
    db.flush()
    loan = Loan(
        member_id=member.id,
        principal=Decimal("100.00"),
        monthly_rate=Decimal("0.20"),
        installments=1,
        status="RESTRUCTURED",
    )
    db.add(loan)
    db.flush()
    agreement = CollectionAgreement(
        loan_id=loan.id,
        member_id=member.id,
        requested_by=user.id,
        status="APPROVED",
        installments=1,
        total_amount=Decimal("105.00"),
        snapshot="{}",
    )
    db.add(agreement)
    db.flush()
    installment = AgreementInstallment(
        agreement_id=agreement.id,
        number=1,
        due_date=date(2026, 9, 1),
        principal=Decimal("100.00"),
        penalty_amount=Decimal("5.00"),
        amount=Decimal("105.00"),
        paid_amount=Decimal("0.00"),
        paid_penalty_amount=Decimal("0.00"),
        status="OPEN",
    )
    db.add(installment)
    db.commit()
    return user, member, loan, agreement, installment


def _provider_result(suffix):
    return {
        "id": f"agreement-provider-payment-{suffix}",
        "order_id": f"agreement-provider-order-{suffix}",
        "status": "pending",
        "qr_code": f"agreement-qr-{suffix}",
        "qr_code_base64": f"agreement-qr-base64-{suffix}",
        "ticket_url": f"https://provider.invalid/agreement/{suffix}",
    }


def _reserved_fallback(db, installment, suffix):
    _, locked = pix_attempts.lock_installment(db, installment.id)
    payment, created = pix_attempts.reserve(db, locked, Decimal("105.00"))
    assert created is True
    result = _provider_result(suffix)
    payment = pix_attempts.preserve_provider_result_for_reconciliation(
        db, payment.id, installment.id, result
    )
    return payment, result


def _no_financial_effect(db, payment, installment, agreement):
    db.refresh(payment)
    db.refresh(installment)
    db.refresh(agreement)
    assert installment.status == "OPEN"
    assert installment.paid_amount == Decimal("0.00")
    assert installment.paid_penalty_amount == Decimal("0.00")
    assert agreement.status == "APPROVED"
    assert db.query(PaymentSettlement).filter_by(payment_id=payment.id).count() == 0
    assert db.query(LedgerEntry).filter_by(reference_id=str(payment.id)).count() == 0
    assert db.query(PaymentReversal).filter_by(payment_id=payment.id).count() == 0


def test_reservation_is_durable_before_network_and_success_binds_same_payment(
    tmp_path, monkeypatch
):
    engine, Session = _database(tmp_path)
    db = Session()
    user, _, _, _, installment = _seed(db, "durable")
    observed = {}
    network_calls = []

    async def provider(self, **kwargs):
        network_calls.append(kwargs)
        observer = Session()
        try:
            reserved = observer.query(Payment).filter_by(
                reference_type="AGREEMENT_INSTALLMENT",
                reference_id=str(installment.id),
                attempt_status="PENDING",
            ).one()
            observed.update(
                payment_id=reserved.id,
                status=reserved.status,
                provider_order_id=reserved.provider_order_id,
                provider_payment_id=reserved.provider_payment_id,
                idempotency_key=reserved.idempotency_key,
                external_reference=reserved.external_reference,
                amount=reserved.amount,
                network_key=kwargs["idempotency_key"],
                network_reference=kwargs["external_reference"],
                network_amount=kwargs["amount"],
                pending_count=observer.query(Payment).filter_by(
                    reference_type="AGREEMENT_INSTALLMENT",
                    reference_id=str(installment.id),
                    attempt_status="PENDING",
                ).count(),
            )
        finally:
            observer.close()
        return _provider_result("durable")

    monkeypatch.setattr(
        agreement_pix_api.MercadoPagoClient, "create_pix_payment", provider
    )
    response = asyncio.run(
        agreement_pix_api.create_pix(installment.id, user, db)
    )

    payment = db.query(Payment).filter_by(
        reference_type="AGREEMENT_INSTALLMENT",
        reference_id=str(installment.id),
    ).one()
    assert observed["payment_id"] == payment.id == response["payment_id"]
    assert observed["status"] == "PENDING"
    assert observed["provider_order_id"] is None
    assert observed["provider_payment_id"] is None
    assert observed["pending_count"] == 1
    assert observed["idempotency_key"] == observed["network_key"] == (
        f"frc-agreement-installment-{installment.id}"
    )
    assert observed["external_reference"] == observed["network_reference"] == (
        f"agreement_installment:{installment.id}"
    )
    assert observed["amount"] == observed["network_amount"] == Decimal("105.00")
    assert payment.provider_order_id == "agreement-provider-order-durable"
    assert payment.provider_payment_id == "agreement-provider-payment-durable"
    assert payment.qr_code == "agreement-qr-durable"
    assert payment.qr_code_base64 == "agreement-qr-base64-durable"
    assert payment.ticket_url == "https://provider.invalid/agreement/durable"
    assert payment.attempt_status is None
    assert payment.reconciliation_status is None
    assert db.query(Payment).filter_by(
        reference_type="AGREEMENT_INSTALLMENT",
        reference_id=str(installment.id),
    ).count() == 1
    assert db.query(Payment).filter_by(
        reference_type="AGREEMENT_INSTALLMENT",
        reference_id=str(installment.id),
        attempt_status="PENDING",
    ).count() == 0

    async def forbidden(self, **kwargs):
        network_calls.append(kwargs)
        raise AssertionError("provider-backed pending payment must be reused")

    monkeypatch.setattr(
        agreement_pix_api.MercadoPagoClient, "create_pix_payment", forbidden
    )
    second = asyncio.run(
        agreement_pix_api.create_pix(installment.id, user, db)
    )
    assert second["payment_id"] == payment.id
    assert len(network_calls) == 1
    _no_financial_effect(db, payment, installment, db.get(CollectionAgreement, installment.agreement_id))
    db.close()
    engine.dispose()


@pytest.mark.parametrize(
    "failure,reconciliation_status",
    [
        ("ambiguous", "PROVIDER_CREATE_UNKNOWN"),
        ("known", "RECONCILIATION_REQUIRED"),
    ],
)
def test_create_failure_keeps_reservation_and_second_call_does_not_repost(
    tmp_path, monkeypatch, failure, reconciliation_status
):
    engine, Session = _database(tmp_path)
    db = Session()
    user, _, _, agreement, installment = _seed(db, f"failure-{failure}")
    calls = []

    async def provider(self, **kwargs):
        calls.append(kwargs)
        if failure == "ambiguous":
            raise ProviderCreateAmbiguity("synthetic-token cpf email payload")
        raise RuntimeError("synthetic known provider error")

    monkeypatch.setattr(
        agreement_pix_api.MercadoPagoClient, "create_pix_payment", provider
    )
    with pytest.raises(HTTPException) as first:
        asyncio.run(agreement_pix_api.create_pix(installment.id, user, db))
    assert first.value.status_code == 502
    assert "synthetic-token" not in first.value.detail
    assert "cpf" not in first.value.detail
    assert "email" not in first.value.detail

    payment = db.query(Payment).filter_by(
        reference_type="AGREEMENT_INSTALLMENT",
        reference_id=str(installment.id),
    ).one()
    payment_id = payment.id
    key = f"frc-agreement-installment-{installment.id}"
    assert payment.provider_payment_id is None
    assert payment.provider_order_id is None
    assert payment.idempotency_key == key
    assert payment.attempt_status == "PENDING"
    assert payment.reconciliation_status == reconciliation_status

    async def forbidden(self, **kwargs):
        calls.append(kwargs)
        raise AssertionError("no repost is allowed for a reserved attempt")

    monkeypatch.setattr(
        agreement_pix_api.MercadoPagoClient, "create_pix_payment", forbidden
    )
    with pytest.raises(HTTPException) as second:
        asyncio.run(agreement_pix_api.create_pix(installment.id, user, db))
    assert second.value.status_code == 409
    assert len(calls) == 1
    same = db.query(Payment).filter_by(id=payment_id).one()
    assert same.idempotency_key == key
    assert same.attempt_status == "PENDING"
    assert same.reconciliation_status == reconciliation_status
    _no_financial_effect(db, same, installment, agreement)
    db.close()
    engine.dispose()


def test_bind_commit_failure_preserves_provider_identity_and_blocks_repost(
    tmp_path, monkeypatch
):
    engine, Session = _database(tmp_path)
    db = Session()
    user, _, _, agreement, installment = _seed(db, "bind-failure")
    calls = []
    placeholder_ids = []
    real_commit = db.commit
    commits_after_network = 0

    async def provider(self, **kwargs):
        calls.append(kwargs)
        observer = Session()
        try:
            placeholder = observer.query(Payment).filter_by(
                reference_type="AGREEMENT_INSTALLMENT",
                reference_id=str(installment.id),
                attempt_status="PENDING",
            ).one()
            placeholder_ids.append(placeholder.id)
        finally:
            observer.close()

        def fail_bind_commit_once():
            nonlocal commits_after_network
            commits_after_network += 1
            if commits_after_network == 1:
                raise RuntimeError("synthetic bind commit failure")
            return real_commit()

        monkeypatch.setattr(db, "commit", fail_bind_commit_once)
        return _provider_result("bind-failure")

    monkeypatch.setattr(
        agreement_pix_api.MercadoPagoClient, "create_pix_payment", provider
    )
    with pytest.raises(HTTPException) as first:
        asyncio.run(agreement_pix_api.create_pix(installment.id, user, db))
    assert first.value.status_code == 502
    payment = db.query(Payment).filter_by(
        reference_type="AGREEMENT_INSTALLMENT",
        reference_id=str(installment.id),
    ).one()
    assert payment.id == placeholder_ids[0]
    assert payment.provider_order_id == "agreement-provider-order-bind-failure"
    assert payment.provider_payment_id == "agreement-provider-payment-bind-failure"
    assert payment.qr_code == "agreement-qr-bind-failure"
    assert payment.qr_code_base64 == "agreement-qr-base64-bind-failure"
    assert payment.ticket_url == "https://provider.invalid/agreement/bind-failure"
    assert payment.status == payment.raw_status == "pending"
    assert payment.idempotency_key == f"frc-agreement-installment-{installment.id}"
    assert payment.external_reference == f"agreement_installment:{installment.id}"
    assert payment.attempt_status == "PENDING"
    assert payment.reconciliation_status == "RECONCILIATION_REQUIRED"
    _no_financial_effect(db, payment, installment, agreement)

    async def forbidden(self, **kwargs):
        calls.append(kwargs)
        raise AssertionError("provider POST repeated after known identity fallback")

    monkeypatch.setattr(
        agreement_pix_api.MercadoPagoClient, "create_pix_payment", forbidden
    )
    second = asyncio.run(
        agreement_pix_api.create_pix(installment.id, user, db)
    )
    assert second["payment_id"] == payment.id
    assert second["provider_payment_id"] == payment.provider_payment_id
    assert len(calls) == 1
    assert db.query(Payment).filter_by(
        reference_type="AGREEMENT_INSTALLMENT",
        reference_id=str(installment.id),
    ).count() == 1
    db.close()
    engine.dispose()


def test_bind_already_committed_survives_post_commit_refresh_failure(
    tmp_path, monkeypatch
):
    engine, Session = _database(tmp_path)
    db = Session()
    user, _, _, _, installment = _seed(db, "refresh-failure")
    real_refresh = db.refresh

    async def provider(self, **kwargs):
        def fail_refresh(instance, *args, **kwargs):
            raise RuntimeError("synthetic refresh failure")

        monkeypatch.setattr(db, "refresh", fail_refresh)
        return _provider_result("refresh-failure")

    monkeypatch.setattr(
        agreement_pix_api.MercadoPagoClient, "create_pix_payment", provider
    )
    response = asyncio.run(agreement_pix_api.create_pix(installment.id, user, db))
    payment = db.query(Payment).filter_by(id=response["payment_id"]).one()
    assert payment.provider_order_id == "agreement-provider-order-refresh-failure"
    assert payment.provider_payment_id == "agreement-provider-payment-refresh-failure"
    assert payment.attempt_status is None
    assert payment.reconciliation_status is None
    db.refresh = real_refresh
    _no_financial_effect(db, payment, installment, db.get(CollectionAgreement, installment.agreement_id))
    db.close()
    engine.dispose()


def test_fallback_rejects_conflicting_provider_identity_and_failure_is_visible(
    tmp_path, monkeypatch
):
    engine, Session = _database(tmp_path)
    db = Session()
    user, _, _, _, installment = _seed(db, "conflicting")
    payment, _ = _reserved_fallback(db, installment, "conflicting")
    conflicting = _provider_result("different")
    with pytest.raises(ValueError, match="conflicting provider order identity"):
        pix_attempts.preserve_provider_result_for_reconciliation(
            db, payment.id, installment.id, conflicting
        )
    db.refresh(payment)
    assert payment.provider_order_id == "agreement-provider-order-conflicting"
    assert payment.provider_payment_id == "agreement-provider-payment-conflicting"
    db.close()
    engine.dispose()

    fallback_dir = tmp_path / "fallback-failure"
    fallback_dir.mkdir()
    engine, Session = _database(fallback_dir)
    db = Session()
    user, _, _, _, installment = _seed(db, "fallback-write-failure")
    calls = []

    async def provider(self, **kwargs):
        calls.append(kwargs)

        def failed_commit():
            raise RuntimeError("synthetic fallback persistence failure")

        monkeypatch.setattr(db, "commit", failed_commit)
        return _provider_result("fallback-write-failure")

    monkeypatch.setattr(
        agreement_pix_api.MercadoPagoClient, "create_pix_payment", provider
    )
    with pytest.raises(RuntimeError, match="synthetic fallback persistence failure"):
        asyncio.run(agreement_pix_api.create_pix(installment.id, user, db))
    assert len(calls) == 1
    db.rollback()
    placeholder = db.query(Payment).filter_by(
        reference_type="AGREEMENT_INSTALLMENT",
        reference_id=str(installment.id),
    ).one()
    assert placeholder.attempt_status == "PENDING"
    assert placeholder.provider_order_id is None
    assert placeholder.provider_payment_id is None
    db.close()
    engine.dispose()


@pytest.mark.parametrize(
    "remote_status,status_detail",
    [
        ("pending", None),
        ("in_process", None),
        ("processed", None),
        ("processed", "pending_waiting_transfer"),
        ("processed", "other_detail"),
        ("refunded", None),
        ("charged_back", None),
        ("rejected", None),
        ("failed", None),
        ("cancelled", None),
    ],
)
def test_nonconfirmed_canonical_status_does_not_settle_or_release_attempt(
    tmp_path, monkeypatch, remote_status, status_detail
):
    engine, Session = _database(tmp_path)
    db = Session()
    _, _, _, agreement, installment = _seed(db, f"remote-{remote_status}")
    suffix = f"{remote_status}-{status_detail or 'none'}"
    payment, result = _reserved_fallback(db, installment, suffix)
    monkeypatch.setattr(
        payments_api,
        "validate_mercado_pago_signature",
        lambda *args, **kwargs: True,
    )

    async def get_order(self, order_id):
        return {
            "id": result["order_id"],
            "status": remote_status,
            "transactions": {
                "payments": [
                    {
                        "id": result["id"],
                        "status": remote_status,
                        "transaction_amount": "105.00",
                        **(
                            {"status_detail": status_detail}
                            if status_detail is not None
                            else {}
                        ),
                    }
                ]
            },
        }

    monkeypatch.setattr(payments_api.MercadoPagoClient, "get_order", get_order)
    response = asyncio.run(
        payments_api.mercado_pago_webhook(
            _WebhookRequest("order", f"event-{remote_status}", result["order_id"]),
            db,
        )
    )
    db.refresh(payment)
    assert response["received"] is True
    assert payment.status == remote_status
    assert payment.attempt_status == "PENDING"
    assert payment.reconciliation_status == "RECONCILIATION_REQUIRED"
    event = db.query(WebhookEvent).filter_by(
        provider="mercado_pago", event_id=f"event-{remote_status}"
    ).one()
    assert event.processed is True
    assert event.resource_id is None
    assert db.query(PaymentSettlement).filter_by(payment_id=payment.id).count() == 0
    assert db.query(PaymentReversal).filter_by(payment_id=payment.id).count() == 0
    _no_financial_effect(db, payment, installment, agreement)
    db.close()
    engine.dispose()


@pytest.mark.parametrize(
    "remote_status,status_detail",
    [("approved", None), ("processed", "accredited")],
)
def test_canonical_confirmation_settles_and_closes_lifecycle_once(
    tmp_path, monkeypatch, remote_status, status_detail
):
    engine, Session = _database(tmp_path)
    db = Session()
    _, _, _, agreement, installment = _seed(db, f"confirmed-{remote_status}")
    payment, result = _reserved_fallback(db, installment, remote_status)
    monkeypatch.setattr(
        payments_api,
        "validate_mercado_pago_signature",
        lambda *args, **kwargs: True,
    )
    get_order_calls = []

    async def get_order(self, order_id):
        get_order_calls.append(order_id)
        remote_payment = {
            "id": result["id"],
            "status": remote_status,
            "transaction_amount": "105.00",
            "date_approved": "2026-09-27T12:00:00Z",
        }
        if status_detail is not None:
            remote_payment["status_detail"] = status_detail
        return {
            "id": result["order_id"],
            "status": remote_status,
            "transactions": {"payments": [remote_payment]},
        }

    monkeypatch.setattr(payments_api.MercadoPagoClient, "get_order", get_order)
    first = asyncio.run(
        payments_api.mercado_pago_webhook(
            _WebhookRequest("payment", f"confirmed-payment-{remote_status}", result["id"]),
            db,
        )
    )
    db.refresh(payment)
    db.refresh(installment)
    db.refresh(agreement)
    assert first["received"] is True
    assert payment.attempt_status is None
    assert payment.reconciliation_status is None
    assert installment.status == "PAID"
    assert installment.paid_amount == Decimal("100.00")
    assert installment.paid_penalty_amount == Decimal("5.00")
    assert agreement.status == "SETTLED"
    assert db.query(PaymentSettlement).filter_by(payment_id=payment.id).count() == 1
    event = db.query(WebhookEvent).filter_by(
        provider="mercado_pago", event_id=f"confirmed-payment-{remote_status}"
    ).one()
    assert event.processed is True
    assert event.resource_id is None
    assert db.query(Payment).filter_by(
        reference_type="AGREEMENT_INSTALLMENT",
        reference_id=str(installment.id),
        attempt_status="PENDING",
    ).count() == 0
    first_ledger_count = db.query(LedgerEntry).filter_by(
        reference_id=str(payment.id)
    ).count()
    assert first_ledger_count > 0
    assert db.query(LedgerEntry).filter_by(
        reference_type="AGREEMENT_INSTALLMENT_PAYMENT",
        reference_id=str(payment.id),
    ).count() == 1
    first_revision = agreement.state_revision
    assert first_revision == 1

    second = asyncio.run(
        payments_api.mercado_pago_webhook(
            _WebhookRequest("order", f"confirmed-order-{remote_status}", result["order_id"]),
            db,
        )
    )
    db.refresh(payment)
    db.refresh(installment)
    db.refresh(agreement)
    assert second["received"] is True
    assert len(get_order_calls) == 2
    assert payment.attempt_status is None
    assert payment.reconciliation_status is None
    assert installment.paid_amount == Decimal("100.00")
    assert installment.paid_penalty_amount == Decimal("5.00")
    assert agreement.state_revision == first_revision
    assert db.query(PaymentSettlement).filter_by(payment_id=payment.id).count() == 1
    assert db.query(LedgerEntry).filter_by(reference_id=str(payment.id)).count() == first_ledger_count
    db.close()
    engine.dispose()


def _synthetic_valid_cpf(seed):
    base = f"{int(seed, 16) % 1_000_000_000:09d}"
    if len(set(base)) == 1:
        base = "123456789"
    first_sum = sum(int(base[index]) * (10 - index) for index in range(9))
    first_digit = 0 if first_sum % 11 < 2 else 11 - first_sum % 11
    first_ten = base + str(first_digit)
    second_sum = sum(int(first_ten[index]) * (11 - index) for index in range(10))
    second_digit = 0 if second_sum % 11 < 2 else 11 - second_sum % 11
    return first_ten + str(second_digit)


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith("postgresql"),
    reason="requires the PostgreSQL CI database",
)
def test_postgresql_concurrent_agreement_pix_reservations_share_one_winner():
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    setup = Session()
    suffix = uuid.uuid4().hex
    user = User(
        name=f"PG Agreement {suffix}",
        email=f"pg-agreement-{suffix}@test.invalid",
        cpf=_synthetic_valid_cpf(suffix),
        password_hash="synthetic-password-hash",
        is_active=True,
    )
    group = Group(name=f"PG Agreement group {suffix}")
    setup.add_all([user, group])
    setup.flush()
    member = Member(user_id=user.id, group_id=group.id, status="ACTIVE")
    setup.add(member)
    setup.flush()
    loan = Loan(
        member_id=member.id,
        principal=Decimal("100.00"),
        monthly_rate=Decimal("0.20"),
        installments=1,
        status="RESTRUCTURED",
    )
    setup.add(loan)
    setup.flush()
    agreement = CollectionAgreement(
        loan_id=loan.id,
        member_id=member.id,
        requested_by=user.id,
        status="APPROVED",
        installments=1,
        total_amount=Decimal("105.00"),
        snapshot="{}",
    )
    setup.add(agreement)
    setup.flush()
    installment = AgreementInstallment(
        agreement_id=agreement.id,
        number=1,
        due_date=date(2026, 9, 1),
        principal=Decimal("100.00"),
        penalty_amount=Decimal("5.00"),
        amount=Decimal("105.00"),
        status="OPEN",
    )
    setup.add(installment)
    setup.commit()
    installment_id = installment.id
    member_id = member.id
    loan_id = loan.id
    agreement_id = agreement.id
    user_id = user.id
    group_id = group.id
    setup.close()

    barrier = threading.Barrier(2)

    def reserve_concurrently():
        session = Session()
        try:
            barrier.wait(timeout=10)
            _, locked = pix_attempts.lock_installment(session, installment_id)
            payment, created = pix_attempts.reserve(
                session, locked, Decimal("105.00")
            )
            return payment.id, created
        finally:
            session.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: reserve_concurrently(), range(2)))
        verify = Session()
        try:
            pending = verify.query(Payment).filter_by(
                reference_type="AGREEMENT_INSTALLMENT",
                reference_id=str(installment_id),
                attempt_status="PENDING",
            ).all()
            assert len(pending) == 1
            assert results[0][0] == results[1][0] == pending[0].id
            assert sorted(created for _, created in results) == [False, True]
        finally:
            verify.close()
    finally:
        cleanup = Session()
        try:
            cleanup.query(Payment).filter_by(
                reference_type="AGREEMENT_INSTALLMENT",
                reference_id=str(installment_id),
            ).delete()
            cleanup.query(AgreementInstallment).filter_by(id=installment_id).delete()
            cleanup.query(CollectionAgreement).filter_by(id=agreement_id).delete()
            cleanup.query(Loan).filter_by(id=loan_id).delete()
            cleanup.query(Member).filter_by(id=member_id).delete()
            cleanup.query(User).filter_by(id=user_id).delete()
            cleanup.query(Group).filter_by(id=group_id).delete()
            cleanup.commit()
        finally:
            cleanup.close()
            engine.dispose()
