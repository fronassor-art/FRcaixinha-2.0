"""F12D durable Contribution PIX reservation contract tests."""

import asyncio
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import payments as payments_api
from app.db.base import Base
from app.models import (
    Contribution,
    Group,
    LedgerEntry,
    Member,
    Payment,
    PaymentReversal,
    PaymentSettlement,
    User,
)
from app.services import contribution_pix_attempts
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
        f"sqlite:///{tmp_path / 'contribution-pix.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _seed(db, suffix=None):
    suffix = suffix or uuid.uuid4().hex
    user = User(
        name=f"Contribution {suffix}",
        email=f"contribution-{suffix}@test.invalid",
        cpf=f"cpf-{suffix}",
        password_hash="synthetic-password-hash",
        is_active=True,
    )
    group = Group(name=f"Contribution group {suffix}")
    db.add_all([user, group])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id, status="ACTIVE")
    db.add(member)
    db.flush()
    contribution = Contribution(
        member_id=member.id,
        competence=date(2026, 9, 1),
        amount=Decimal("25.00"),
        paid_amount=Decimal("0.00"),
        status="PENDING",
    )
    db.add(contribution)
    db.commit()
    return user, member, contribution


def _pix_result(suffix="success"):
    return {
        "id": f"provider-payment-{suffix}",
        "order_id": f"provider-order-{suffix}",
        "status": "pending",
        "qr_code": f"qr-{suffix}",
        "qr_code_base64": f"qr-base64-{suffix}",
        "ticket_url": f"https://provider.invalid/ticket/{suffix}",
    }


def _synthetic_valid_cpf(seed):
    """Build deterministic synthetic digits using the app's CPF checksum."""
    base = f"{int(seed, 16) % 1_000_000_000:09d}"
    if len(set(base)) == 1:
        base = "123456789"
    first_sum = sum(int(base[index]) * (10 - index) for index in range(9))
    first_digit = 0 if first_sum % 11 < 2 else 11 - first_sum % 11
    first_ten = base + str(first_digit)
    second_sum = sum(int(first_ten[index]) * (11 - index) for index in range(10))
    second_digit = 0 if second_sum % 11 < 2 else 11 - second_sum % 11
    return first_ten + str(second_digit)


def _assert_no_financial_effect(db, contribution, payment):
    db.refresh(contribution)
    db.refresh(payment)
    assert contribution.status == "PENDING"
    assert contribution.paid_amount == Decimal("0.00")
    assert payment.ledger_posted_at is None
    assert db.query(PaymentSettlement).filter_by(payment_id=payment.id).count() == 0
    assert db.query(LedgerEntry).filter_by(reference_id=str(payment.id)).count() == 0


def _reserved_fallback_payment(db, contribution, suffix):
    payment, created = contribution_pix_attempts.reserve(
        db, contribution.id, Decimal("25.00")
    )
    assert created is True
    result = _pix_result(suffix)
    payment = contribution_pix_attempts.preserve_provider_result_for_reconciliation(
        db, payment.id, contribution.id, result
    )
    return payment, result


def test_reservation_is_committed_before_network_and_success_binds_same_payment(
    tmp_path, monkeypatch
):
    engine, Session = _database(tmp_path)
    db = Session()
    user, _, contribution = _seed(db, "durable-success")
    seen = {}

    async def provider(self, **kwargs):
        # A separate Session and SQLite connection can see only committed TX1.
        observer = Session()
        try:
            reserved = observer.query(Payment).filter_by(
                reference_type="CONTRIBUTION",
                reference_id=str(contribution.id),
                attempt_status="PENDING",
            ).one()
            durable_contribution = observer.get(Contribution, contribution.id)
            seen.update(
                payment_id=reserved.id,
                provider_payment_id=reserved.provider_payment_id,
                provider_order_id=reserved.provider_order_id,
                attempt_status=reserved.attempt_status,
                reconciliation_status=reserved.reconciliation_status,
                pending_count=observer.query(Payment).filter_by(
                    reference_type="CONTRIBUTION",
                    reference_id=str(contribution.id),
                    attempt_status="PENDING",
                ).count(),
                key=reserved.idempotency_key,
                contribution_key=durable_contribution.pix_idempotency_key,
                amount=reserved.amount,
                external_reference=reserved.external_reference,
                network_key=kwargs["idempotency_key"],
                network_external_reference=kwargs["external_reference"],
                network_amount=kwargs["amount"],
            )
        finally:
            observer.close()
        return _pix_result("durable-success")

    monkeypatch.setattr(payments_api.MercadoPagoClient, "create_pix_payment", provider)
    response = asyncio.run(payments_api.create_pix(contribution.id, user, db))

    payments = db.query(Payment).filter_by(
        reference_type="CONTRIBUTION", reference_id=str(contribution.id)
    ).all()
    payment = payments[0]
    assert len(payments) == 1
    assert seen["payment_id"] == payment.id == response["payment_id"]
    assert seen["provider_payment_id"] is None
    assert seen["provider_order_id"] is None
    assert seen["attempt_status"] == "PENDING"
    assert seen["pending_count"] == 1
    assert seen["reconciliation_status"] is None
    assert seen["key"] == seen["contribution_key"] == f"frc-contribution-{contribution.id}"
    assert seen["network_key"] == seen["key"]
    assert seen["external_reference"] == seen["network_external_reference"] == f"contribution-{contribution.id}"
    assert seen["network_amount"] == seen["amount"] == Decimal("25.00")
    assert payment.provider_order_id == "provider-order-durable-success"
    assert payment.provider_payment_id == "provider-payment-durable-success"
    assert payment.qr_code == "qr-durable-success"
    assert payment.qr_code_base64 == "qr-base64-durable-success"
    assert payment.ticket_url == "https://provider.invalid/ticket/durable-success"
    assert payment.reconciliation_status is None
    assert payment.attempt_status is None
    assert db.query(Payment).filter_by(
        reference_type="CONTRIBUTION",
        reference_id=str(contribution.id),
        attempt_status="PENDING",
    ).count() == 0
    assert db.query(Payment).filter_by(
        reference_type="CONTRIBUTION", reference_id=str(contribution.id)
    ).count() == 1
    db.refresh(contribution)
    assert contribution.payment_id == payment.id
    _assert_no_financial_effect(db, contribution, payment)
    db.close()
    engine.dispose()


def test_bind_commit_failure_preserves_provider_identity_and_blocks_repost(
    tmp_path, monkeypatch
):
    engine, Session = _database(tmp_path)
    db = Session()
    user, _, contribution = _seed(db, "bind-commit-failure")
    calls = []
    placeholder_ids = []
    commit = db.commit
    commit_calls = 0

    async def provider(self, **kwargs):
        calls.append(kwargs)
        observer = Session()
        try:
            placeholder = observer.query(Payment).filter_by(
                reference_type="CONTRIBUTION",
                reference_id=str(contribution.id),
                attempt_status="PENDING",
            ).one()
            placeholder_ids.append(placeholder.id)
        finally:
            observer.close()
        # TX1 already committed before this callback; fail the first commit
        # after it, which is the provider bind commit. The fallback commit is
        # then allowed to succeed.
        def fail_first_bind_commit():
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 1:
                raise RuntimeError("synthetic bind commit failure")
            return commit()

        monkeypatch.setattr(db, "commit", fail_first_bind_commit)
        return _pix_result("bind-commit-failure")

    monkeypatch.setattr(payments_api.MercadoPagoClient, "create_pix_payment", provider)
    with pytest.raises(HTTPException) as bind_error:
        asyncio.run(payments_api.create_pix(contribution.id, user, db))
    assert bind_error.value.status_code == 502

    payment = db.query(Payment).filter_by(
        reference_type="CONTRIBUTION", reference_id=str(contribution.id)
    ).one()
    assert payment.id == placeholder_ids[0]
    assert payment.provider_order_id == "provider-order-bind-commit-failure"
    assert payment.provider_payment_id == "provider-payment-bind-commit-failure"
    assert payment.idempotency_key == f"frc-contribution-{contribution.id}"
    assert payment.external_reference == f"contribution-{contribution.id}"
    assert payment.qr_code == "qr-bind-commit-failure"
    assert payment.qr_code_base64 == "qr-base64-bind-commit-failure"
    assert payment.ticket_url == "https://provider.invalid/ticket/bind-commit-failure"
    assert payment.status == payment.raw_status == "pending"
    assert payment.attempt_status == "PENDING"
    assert payment.reconciliation_status == "RECONCILIATION_REQUIRED"
    assert db.query(Payment).filter_by(
        reference_type="CONTRIBUTION", reference_id=str(contribution.id)
    ).count() == 1
    _assert_no_financial_effect(db, contribution, payment)

    async def forbidden(self, **kwargs):
        calls.append(kwargs)
        raise AssertionError("provider POST must not be repeated after bind failure")

    monkeypatch.setattr(payments_api.MercadoPagoClient, "create_pix_payment", forbidden)
    response = asyncio.run(payments_api.create_pix(contribution.id, user, db))
    assert response["payment_id"] == payment.id
    assert response["provider_payment_id"] == payment.provider_payment_id
    assert len(calls) == 1
    assert db.query(Payment).filter_by(
        reference_type="CONTRIBUTION", reference_id=str(contribution.id)
    ).count() == 1
    db.close()
    engine.dispose()


def test_committed_bind_survives_refresh_failure_without_degradation(
    tmp_path, monkeypatch
):
    engine, Session = _database(tmp_path)
    db = Session()
    user, _, contribution = _seed(db, "bind-refresh-failure")
    calls = []
    refresh = db.refresh

    async def provider(self, **kwargs):
        calls.append(kwargs)

        def fail_bind_refresh(instance, *args, **kwargs):
            raise RuntimeError("synthetic post-commit refresh failure")

        monkeypatch.setattr(db, "refresh", fail_bind_refresh)
        return _pix_result("bind-refresh-failure")

    monkeypatch.setattr(payments_api.MercadoPagoClient, "create_pix_payment", provider)
    response = asyncio.run(payments_api.create_pix(contribution.id, user, db))
    assert response["provider_payment_id"] == "provider-payment-bind-refresh-failure"

    payment = db.query(Payment).filter_by(
        reference_type="CONTRIBUTION", reference_id=str(contribution.id)
    ).one()
    assert payment.provider_order_id == "provider-order-bind-refresh-failure"
    assert payment.provider_payment_id == "provider-payment-bind-refresh-failure"
    assert payment.attempt_status is None
    assert payment.reconciliation_status is None
    db.refresh = refresh
    db.refresh(contribution)
    assert contribution.payment_id == payment.id
    assert len(calls) == 1
    _assert_no_financial_effect(db, contribution, payment)
    db.close()
    engine.dispose()


def test_fallback_persistence_failure_is_not_hidden(tmp_path, monkeypatch):
    engine, Session = _database(tmp_path)
    db = Session()
    user, _, contribution = _seed(db, "fallback-failure")
    calls = []

    async def provider(self, **kwargs):
        calls.append(kwargs)

        def unavailable_commit():
            raise RuntimeError("synthetic database write failure")

        monkeypatch.setattr(db, "commit", unavailable_commit)
        return _pix_result("fallback-failure")

    monkeypatch.setattr(payments_api.MercadoPagoClient, "create_pix_payment", provider)
    with pytest.raises(RuntimeError, match="synthetic database write failure"):
        asyncio.run(payments_api.create_pix(contribution.id, user, db))
    assert len(calls) == 1
    db.rollback()
    payment = db.query(Payment).filter_by(
        reference_type="CONTRIBUTION", reference_id=str(contribution.id)
    ).one()
    assert payment.provider_order_id is None
    assert payment.provider_payment_id is None
    assert payment.attempt_status == "PENDING"
    assert db.query(Payment).filter_by(
        reference_type="CONTRIBUTION", reference_id=str(contribution.id)
    ).count() == 1
    db.close()
    engine.dispose()


def test_ambiguous_create_persists_and_blocks_blind_repost(tmp_path, monkeypatch):
    engine, Session = _database(tmp_path)
    db = Session()
    user, _, contribution = _seed(db, "ambiguous")
    calls = []

    async def ambiguous(self, **kwargs):
        calls.append(kwargs)
        raise ProviderCreateAmbiguity(
            "provider response contains synthetic-token, cpf-123, payer@test.invalid"
        )

    monkeypatch.setattr(payments_api.MercadoPagoClient, "create_pix_payment", ambiguous)
    with pytest.raises(HTTPException) as first_error:
        asyncio.run(payments_api.create_pix(contribution.id, user, db))
    assert first_error.value.status_code == 502
    assert "synthetic-token" not in first_error.value.detail
    assert "cpf-123" not in first_error.value.detail
    assert "payer@test.invalid" not in first_error.value.detail

    payment = db.query(Payment).filter_by(
        reference_type="CONTRIBUTION", reference_id=str(contribution.id)
    ).one()
    assert payment.provider_payment_id is None
    assert payment.provider_order_id is None
    assert payment.attempt_status == "PENDING"
    assert payment.reconciliation_status == "PROVIDER_CREATE_UNKNOWN"
    assert payment.idempotency_key == f"frc-contribution-{contribution.id}"
    first_payment_id = payment.id
    assert db.query(Payment).filter_by(reference_type="CONTRIBUTION", reference_id=str(contribution.id)).count() == 1
    _assert_no_financial_effect(db, contribution, payment)

    async def forbidden(self, **kwargs):
        calls.append(kwargs)
        raise AssertionError("no provider request is allowed after ambiguity")

    monkeypatch.setattr(payments_api.MercadoPagoClient, "create_pix_payment", forbidden)
    with pytest.raises(HTTPException) as second_error:
        asyncio.run(payments_api.create_pix(contribution.id, user, db))
    assert second_error.value.status_code == 409
    assert second_error.value.detail == "Criação Pix pendente de reconciliação."
    assert len(calls) == 1
    same_payment = db.query(Payment).filter_by(
        reference_type="CONTRIBUTION", reference_id=str(contribution.id)
    ).one()
    assert same_payment.id == first_payment_id
    assert same_payment.idempotency_key == f"frc-contribution-{contribution.id}"
    assert db.query(Payment).filter_by(reference_type="CONTRIBUTION", reference_id=str(contribution.id)).count() == 1
    db.close()
    engine.dispose()


def test_known_provider_error_requires_reconciliation_and_blocks_repost(
    tmp_path, monkeypatch
):
    engine, Session = _database(tmp_path)
    db = Session()
    user, _, contribution = _seed(db, "known-error")
    calls = []

    async def known_http_error(self, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("Mercado Pago Orders create returned HTTP response: HTTP 422")

    monkeypatch.setattr(payments_api.MercadoPagoClient, "create_pix_payment", known_http_error)
    with pytest.raises(HTTPException) as first_error:
        asyncio.run(payments_api.create_pix(contribution.id, user, db))
    assert first_error.value.status_code == 502
    assert "422" not in first_error.value.detail

    payment = db.query(Payment).filter_by(
        reference_type="CONTRIBUTION", reference_id=str(contribution.id)
    ).one()
    assert payment.attempt_status == "PENDING"
    assert payment.provider_payment_id is None
    assert payment.reconciliation_status == "RECONCILIATION_REQUIRED"
    first_key = payment.idempotency_key
    first_payment_id = payment.id

    async def forbidden(self, **kwargs):
        calls.append(kwargs)
        raise AssertionError("known HTTP error must not be reposted automatically")

    monkeypatch.setattr(payments_api.MercadoPagoClient, "create_pix_payment", forbidden)
    with pytest.raises(HTTPException) as second_error:
        asyncio.run(payments_api.create_pix(contribution.id, user, db))
    assert second_error.value.status_code == 409
    assert len(calls) == 1
    db.refresh(payment)
    assert payment.id == first_payment_id
    assert payment.attempt_status == "PENDING"
    assert payment.reconciliation_status == "RECONCILIATION_REQUIRED"
    assert payment.idempotency_key == first_key
    assert db.query(Payment).filter_by(reference_type="CONTRIBUTION", reference_id=str(contribution.id)).count() == 1
    _assert_no_financial_effect(db, contribution, payment)
    db.close()
    engine.dispose()


@pytest.mark.parametrize("status", ["pending", "in_process", "PENDING"])
def test_legacy_provider_backed_contribution_payment_is_reused(
    tmp_path, monkeypatch, status
):
    engine, Session = _database(tmp_path)
    db = Session()
    user, _, contribution = _seed(db, f"legacy-{status}")
    payment = Payment(
        provider="mercado_pago",
        provider_order_id=f"legacy-order-{status}",
        provider_payment_id=f"legacy-payment-{status}",
        idempotency_key=f"legacy-key-{status}",
        amount=Decimal("25.00"),
        status=status,
        raw_status=status,
        qr_code="legacy-qr",
        reference_type="CONTRIBUTION",
        reference_id=str(contribution.id),
    )
    db.add(payment)
    db.flush()
    contribution.payment_id = payment.id
    db.commit()

    async def forbidden(self, **kwargs):
        raise AssertionError("provider-backed pending charge must be reused")

    monkeypatch.setattr(payments_api.MercadoPagoClient, "create_pix_payment", forbidden)
    response = asyncio.run(payments_api.create_pix(contribution.id, user, db))
    assert response["payment_id"] == payment.id
    assert response["provider_payment_id"] == payment.provider_payment_id
    assert db.query(Payment).filter_by(reference_type="CONTRIBUTION", reference_id=str(contribution.id)).count() == 1
    db.close()
    engine.dispose()


@pytest.mark.parametrize(
    "event_type,resource_kind,remote_status",
    [
        ("order", "order", "pending"),
        ("payment", "payment", "pending"),
        ("order", "order", "refunded"),
        ("payment", "payment", "charged_back"),
    ],
)
def test_fallback_identity_correlates_webhook_without_early_settlement(
    tmp_path, monkeypatch, event_type, resource_kind, remote_status
):
    engine, Session = _database(tmp_path)
    db = Session()
    _, _, contribution = _seed(db, f"fallback-webhook-{event_type}-{remote_status}")
    payment, result = _reserved_fallback_payment(
        db, contribution, f"fallback-webhook-{event_type}-{remote_status}"
    )
    resource_id = result["order_id"] if resource_kind == "order" else result["id"]
    get_order_calls = []
    monkeypatch.setattr(payments_api, "validate_mercado_pago_signature", lambda *a, **k: True)

    async def get_order(self, order_id):
        get_order_calls.append(order_id)
        return {
            "id": result["order_id"],
            "status": remote_status,
            "transactions": {
                "payments": [
                    {
                        "id": result["id"],
                        "status": remote_status,
                        "transaction_amount": "25.00",
                    }
                ]
            },
        }

    monkeypatch.setattr(payments_api.MercadoPagoClient, "get_order", get_order)
    response = asyncio.run(
        payments_api.mercado_pago_webhook(
            _WebhookRequest(
                event_type,
                f"event-{event_type}-{remote_status}",
                resource_id,
            ),
            db,
        )
    )
    db.refresh(payment)
    db.refresh(contribution)
    assert response["received"] is True
    assert get_order_calls == [result["order_id"]]
    if resource_kind == "order":
        assert db.query(Payment).filter_by(
            provider_order_id=resource_id, provider="mercado_pago"
        ).one().id == payment.id
    else:
        assert db.query(Payment).filter_by(
            provider_payment_id=resource_id, provider="mercado_pago"
        ).one().id == payment.id
    assert payment.attempt_status == "PENDING"
    assert payment.reconciliation_status == "RECONCILIATION_REQUIRED"
    assert db.query(PaymentSettlement).filter_by(payment_id=payment.id).count() == 0
    assert db.query(PaymentReversal).filter_by(payment_id=payment.id).count() == 0
    _assert_no_financial_effect(db, contribution, payment)
    db.close()
    engine.dispose()


@pytest.mark.parametrize(
    "remote_status,status_detail",
    [("approved", None), ("processed", "accredited")],
)
def test_canonical_confirmed_fallback_settlement_closes_lifecycle_once(
    tmp_path, monkeypatch, remote_status, status_detail
):
    engine, Session = _database(tmp_path)
    db = Session()
    _, _, contribution = _seed(db, f"fallback-settled-{remote_status}")
    payment, result = _reserved_fallback_payment(
        db, contribution, f"fallback-settled-{remote_status}"
    )
    monkeypatch.setattr(payments_api, "validate_mercado_pago_signature", lambda *a, **k: True)
    get_order_calls = []

    async def get_order(self, order_id):
        get_order_calls.append(order_id)
        remote_payment = {
            "id": result["id"],
            "status": remote_status,
            "transaction_amount": "25.00",
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
            _WebhookRequest("payment", f"event-confirmed-1-{remote_status}", result["id"]),
            db,
        )
    )
    db.refresh(payment)
    db.refresh(contribution)
    assert first["received"] is True
    assert contribution.paid_amount == Decimal("25.00")
    assert contribution.status == "PAID"
    assert payment.attempt_status is None
    assert payment.reconciliation_status is None
    assert db.query(PaymentSettlement).filter_by(payment_id=payment.id).count() == 1
    first_ledger_count = db.query(LedgerEntry).filter_by(
        reference_id=str(payment.id)
    ).count()
    assert first_ledger_count > 0
    assert db.query(Payment).filter_by(
        reference_type="CONTRIBUTION",
        reference_id=str(contribution.id),
        attempt_status="PENDING",
    ).count() == 0

    second = asyncio.run(
        payments_api.mercado_pago_webhook(
            _WebhookRequest("order", f"event-confirmed-2-{remote_status}", result["order_id"]),
            db,
        )
    )
    db.refresh(payment)
    db.refresh(contribution)
    assert second["received"] is True
    assert len(get_order_calls) == 2
    assert contribution.paid_amount == Decimal("25.00")
    assert db.query(PaymentSettlement).filter_by(payment_id=payment.id).count() == 1
    assert db.query(LedgerEntry).filter_by(reference_id=str(payment.id)).count() == first_ledger_count
    assert payment.attempt_status is None
    assert payment.reconciliation_status is None
    db.close()
    engine.dispose()


def test_sqlite_reservation_race_reload_returns_same_pending_winner(
    tmp_path, monkeypatch
):
    engine, Session = _database(tmp_path)
    db = Session()
    _, _, contribution = _seed(db, "sqlite-race")
    winner, created = contribution_pix_attempts.reserve(
        db, contribution.id, Decimal("25.00")
    )
    real_pending = contribution_pix_attempts._pending_payment
    forced_miss = {"done": False}

    def competing_snapshot_missed(session, contribution_id):
        if not forced_miss["done"]:
            forced_miss["done"] = True
            return None
        return real_pending(session, contribution_id)

    # Model the interleaving where both transactions initially observe no
    # pending row. The partial unique index elects the first commit as winner.
    monkeypatch.setattr(
        contribution_pix_attempts, "_pending_payment", competing_snapshot_missed
    )
    reloaded, second_created = contribution_pix_attempts.reserve(
        db, contribution.id, Decimal("25.00")
    )

    assert created is True
    assert second_created is False
    assert reloaded.id == winner.id
    assert db.query(Payment).filter_by(
        reference_type="CONTRIBUTION",
        reference_id=str(contribution.id),
        attempt_status="PENDING",
    ).count() == 1
    db.close()
    engine.dispose()


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith("postgresql"),
    reason="requires the PostgreSQL CI database",
)
def test_postgresql_concurrent_contribution_reservations_share_one_winner():
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    setup = Session()
    suffix = uuid.uuid4().hex
    user = User(
        name=f"PG Contribution {suffix}",
        email=f"pg-contribution-{suffix}@test.invalid",
        cpf=_synthetic_valid_cpf(suffix),
        password_hash="synthetic-password-hash",
        is_active=True,
    )
    group = Group(name=f"PG Contribution group {suffix}")
    setup.add_all([user, group])
    setup.flush()
    member = Member(user_id=user.id, group_id=group.id, status="ACTIVE")
    setup.add(member)
    setup.flush()
    contribution = Contribution(
        member_id=member.id,
        competence=date(2026, 9, 1),
        amount=Decimal("25.00"),
        paid_amount=Decimal("0.00"),
        status="PENDING",
    )
    setup.add(contribution)
    setup.commit()
    contribution_id = contribution.id
    barrier = threading.Barrier(2)
    setup.close()

    def reserve_concurrently():
        session = Session()
        try:
            barrier.wait(timeout=10)
            payment, created = contribution_pix_attempts.reserve(
                session, contribution_id, Decimal("25.00")
            )
            return payment.id, created
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve_concurrently(), range(2)))

    verify = Session()
    try:
        pending = verify.query(Payment).filter_by(
            reference_type="CONTRIBUTION",
            reference_id=str(contribution_id),
            attempt_status="PENDING",
        ).all()
        assert len(pending) == 1
        assert results[0][0] == results[1][0] == pending[0].id
        assert sorted(created for _, created in results) == [False, True]
    finally:
        # This CI database is disposable; remove only rows created by this test.
        payment_id = pending[0].id
        verify.query(Payment).filter_by(id=payment_id).delete()
        verify.query(Contribution).filter_by(id=contribution_id).delete()
        verify.query(Member).filter_by(id=member.id).delete()
        verify.query(User).filter_by(id=user.id).delete()
        verify.query(Group).filter_by(id=group.id).delete()
        verify.commit()
        verify.close()
        engine.dispose()


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith("postgresql"),
    reason="requires the PostgreSQL CI database",
)
def test_postgresql_provider_identity_fallback_keeps_pending_slot():
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    suffix = uuid.uuid4().hex
    user = User(
        name=f"PG Fallback {suffix}",
        email=f"pg-fallback-{suffix}@test.invalid",
        cpf=_synthetic_valid_cpf(suffix),
        password_hash="synthetic-password-hash",
        is_active=True,
    )
    group = Group(name=f"PG Fallback group {suffix}")
    db.add_all([user, group])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id, status="ACTIVE")
    db.add(member)
    db.flush()
    contribution = Contribution(
        member_id=member.id,
        competence=date(2026, 9, 1),
        amount=Decimal("25.00"),
        paid_amount=Decimal("0.00"),
        status="PENDING",
    )
    db.add(contribution)
    db.commit()
    contribution_id = contribution.id
    try:
        payment, created = contribution_pix_attempts.reserve(
            db, contribution_id, Decimal("25.00")
        )
        assert created is True
        original_payment_id = payment.id
        result = _pix_result(f"pg-fallback-{suffix}")
        payment = contribution_pix_attempts.preserve_provider_result_for_reconciliation(
            db, payment.id, contribution_id, result
        )
        assert payment.id == original_payment_id
        assert payment.provider_order_id == result["order_id"]
        assert payment.provider_payment_id == result["id"]
        assert payment.attempt_status == "PENDING"
        assert payment.reconciliation_status == "RECONCILIATION_REQUIRED"
        assert db.query(Payment).filter_by(
            reference_type="CONTRIBUTION",
            reference_id=str(contribution_id),
            attempt_status="PENDING",
        ).count() == 1
        assert db.query(Payment).filter_by(
            reference_type="CONTRIBUTION", reference_id=str(contribution_id)
        ).count() == 1
    finally:
        db.rollback()
        own_payments = db.query(Payment).filter_by(
            reference_type="CONTRIBUTION", reference_id=str(contribution_id)
        ).all()
        for own_payment in own_payments:
            db.delete(own_payment)
        db.query(Contribution).filter_by(id=contribution_id).delete()
        db.query(Member).filter_by(id=member.id).delete()
        db.query(User).filter_by(id=user.id).delete()
        db.query(Group).filter_by(id=group.id).delete()
        db.commit()
        db.close()
        engine.dispose()
