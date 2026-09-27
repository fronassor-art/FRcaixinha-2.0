"""F12F durable Mercado Pago webhook evidence and replay tests."""

import asyncio
import os
import threading
import time
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import payments as api
from app.api.deps import current_user
from app.core.pix_attempt_v1 import (
    build_loan_installment_snapshot,
    canonical_json,
    financial_date,
    local_expiry_utc,
    snapshot_hash,
)
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import (
    AgreementInstallment,
    CollectionAgreement,
    Contribution,
    Group,
    LedgerEntry,
    Loan,
    LoanInstallment,
    Member,
    Notification,
    Payment,
    PaymentReversal,
    PaymentSettlement,
    User,
    WebhookEvent,
)


class _Request:
    headers = {}

    def __init__(self, event_id, resource_id, event_type="payment"):
        self.query_params = {"data.id": resource_id} if resource_id else {}
        self._data = {
            "id": event_id,
            "type": event_type,
            "data": {"id": resource_id} if resource_id else {},
        }

    async def json(self):
        return self._data


def _database(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'f12f.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _seed_member(db, suffix):
    user = User(
        name=f"F12F {suffix}",
        email=f"f12f-{suffix}@test.invalid",
        cpf=f"cpf-{suffix}",
        password_hash="synthetic-password-hash",
        role="USER",
        is_active=True,
    )
    group = Group(name=f"F12F group {suffix}")
    db.add_all([user, group])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id, status="ACTIVE")
    db.add(member)
    db.flush()
    return user, member


def _seed_contribution(db, suffix):
    _, member = _seed_member(db, suffix)
    contribution = Contribution(
        member_id=member.id,
        competence=date(2026, 9, 1),
        amount=Decimal("10.00"),
        paid_amount=Decimal("0.00"),
        status="PENDING",
    )
    db.add(contribution)
    db.commit()
    return member, contribution


def _seed_agreement(db, suffix):
    user, member = _seed_member(db, suffix)
    loan = Loan(
        member_id=member.id,
        principal=Decimal("10.00"),
        monthly_rate=Decimal("0"),
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
        total_amount=Decimal("10.00"),
        snapshot="{}",
    )
    db.add(agreement)
    db.flush()
    installment = AgreementInstallment(
        agreement_id=agreement.id,
        number=1,
        due_date=date(2026, 1, 1),
        principal=Decimal("10.00"),
        penalty_amount=Decimal("0.00"),
        amount=Decimal("10.00"),
        paid_amount=Decimal("0.00"),
        paid_penalty_amount=Decimal("0.00"),
        status="OPEN",
    )
    db.add(installment)
    db.commit()
    return member, agreement, installment


def _seed_loan(db, suffix):
    _, member = _seed_member(db, suffix)
    loan = Loan(
        member_id=member.id,
        principal=Decimal("10.00"),
        monthly_rate=Decimal("0"),
        installments=1,
        status="ACTIVE",
    )
    db.add(loan)
    db.flush()
    installment = LoanInstallment(
        loan_id=loan.id,
        number=1,
        due_date=date(2026, 1, 1),
        principal=Decimal("10.00"),
        interest=Decimal("0.00"),
        amount=Decimal("10.00"),
        paid_amount=Decimal("0.00"),
        penalty_amount=Decimal("0.00"),
        paid_penalty_amount=Decimal("0.00"),
        status="OPEN",
    )
    db.add(installment)
    db.flush()
    confirmed_at = datetime(2026, 1, 1, 15, 30, tzinfo=timezone.utc)
    calculated_for_date = financial_date(confirmed_at)
    snapshot = build_loan_installment_snapshot(
        loan_id=loan.id,
        installment_id=installment.id,
        installment_number=installment.number,
        calculated_for_date=calculated_for_date,
        principal_due=Decimal("10.00"),
        normal_price_interest_due=Decimal("0.00"),
        fixed_penalty_due=Decimal("0.00"),
        late_interest_due=Decimal("0.00"),
    )
    db.commit()
    return member, loan, installment, calculated_for_date, snapshot, confirmed_at


def _provider_payment(db, suffix, *, reference_type=None, reference_id=None,
                      order_id=None, payment_id=None, attempt_status=None,
                      loan_snapshot=None):
    order_id = order_id or f"f12f-order-{suffix}"
    payment_id = payment_id or f"f12f-payment-{suffix}"
    kwargs = {}
    if loan_snapshot is not None:
        calculated_for_date, snapshot = loan_snapshot
        kwargs.update(
            calculated_for_date=calculated_for_date,
            financial_snapshot_json=canonical_json(snapshot),
            snapshot_hash=snapshot_hash(snapshot),
            expires_at=local_expiry_utc(calculated_for_date),
        )
    payment = Payment(
        provider="mercado_pago",
        provider_order_id=order_id,
        provider_payment_id=payment_id,
        idempotency_key=f"f12f-key-{suffix}",
        external_reference=f"opaque-{suffix}",
        amount=Decimal("10.00"),
        status="pending",
        raw_status="pending",
        reference_type=reference_type,
        reference_id=str(reference_id) if reference_id is not None else None,
        attempt_status=attempt_status,
        **kwargs,
    )
    db.add(payment)
    db.commit()
    return payment


def _valid_signature(monkeypatch):
    monkeypatch.setattr(api, "validate_mercado_pago_signature", lambda *a, **k: True)


def _remote(payment_id, status="approved", **fields):
    provider_payment = {"id": payment_id, "status": status, **fields}
    return {
        "id": "canonical-order-id",
        "status": status,
        "transactions": {"payments": [provider_payment]},
    }


def _admin_replay(db, event_id=None):
    return asyncio.run(
        api.replay_mercado_pago_webhooks(
            event_id=event_id,
            limit=20,
            admin=SimpleNamespace(role="ADMIN"),
            db=db,
        )
    )


def test_valid_webhook_persists_resource_id_and_orphan_remains_retryable(
    tmp_path, monkeypatch
):
    engine, Session = _database(tmp_path)
    db = Session()
    _valid_signature(monkeypatch)
    result = asyncio.run(
        api.mercado_pago_webhook(_Request("orphan-order", "order-before-bind", "order"), db)
    )
    event = db.query(WebhookEvent).filter_by(event_id="orphan-order").one()
    assert result == {"received": True, "reconciliable": True}
    assert event.resource_id == "order-before-bind"
    assert event.event_type == "order"
    assert event.processed is False
    assert db.query(Payment).count() == 0
    assert db.query(PaymentSettlement).count() == 0
    assert db.query(LedgerEntry).count() == 0
    db.close()
    engine.dispose()


def test_invalid_signature_creates_no_webhook_evidence(tmp_path, monkeypatch):
    engine, Session = _database(tmp_path)
    db = Session()
    monkeypatch.setattr(api, "validate_mercado_pago_signature", lambda *a, **k: False)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(api.mercado_pago_webhook(_Request("invalid", "resource-1"), db))
    assert exc_info.value.status_code == 401
    assert db.query(WebhookEvent).count() == 0
    db.close()
    engine.dispose()


@pytest.mark.parametrize("event_type,resource_id", [("order", "order-orphan"), ("payment", "payment-orphan")])
def test_order_and_payment_orphans_are_durable_without_financial_effects(
    tmp_path, monkeypatch, event_type, resource_id
):
    engine, Session = _database(tmp_path)
    db = Session()
    _valid_signature(monkeypatch)
    response = asyncio.run(
        api.mercado_pago_webhook(
            _Request(f"orphan-{event_type}", resource_id, event_type), db
        )
    )
    event = db.query(WebhookEvent).filter_by(event_id=f"orphan-{event_type}").one()
    assert response["reconciliable"] is True
    assert event.processed is False
    assert event.resource_id == resource_id
    assert db.query(Payment).count() == 0
    assert db.query(PaymentSettlement).count() == 0
    assert db.query(LedgerEntry).count() == 0
    db.close()
    engine.dispose()


@pytest.mark.parametrize("event_type", ["order", "payment"])
@pytest.mark.parametrize(
    "status,status_detail",
    [("approved", None), ("processed", "accredited")],
)
def test_replay_correlates_strictly_and_settles_contribution_once(
    tmp_path, monkeypatch, event_type, status, status_detail
):
    engine, Session = _database(tmp_path)
    db = Session()
    _, contribution = _seed_contribution(db, f"{event_type}-{status}-{status_detail}")
    event_id = f"replay-{event_type}-{status}-{status_detail}"
    order_id = f"order-{event_id}"
    payment_id = f"payment-{event_id}"
    _valid_signature(monkeypatch)
    received = asyncio.run(
        api.mercado_pago_webhook(
            _Request(event_id, order_id if event_type == "order" else payment_id, event_type), db
        )
    )
    assert received["reconciliable"] is True
    event = db.query(WebhookEvent).filter_by(event_id=event_id).one()
    assert event.processed is False
    payment = _provider_payment(
        db,
        event_id,
        reference_type="CONTRIBUTION",
        reference_id=contribution.id,
        order_id=order_id,
        payment_id=payment_id,
    )
    remote_fields = {"transaction_amount": "10.00", "date_approved": "2026-09-27T12:00:00Z"}
    if status_detail is not None:
        remote_fields["status_detail"] = status_detail

    async def get_order(self, requested_order_id):
        assert requested_order_id == order_id
        return _remote(payment_id, status, **remote_fields)

    monkeypatch.setattr(api.MercadoPagoClient, "get_order", get_order)
    first = _admin_replay(db, event_id)
    second = _admin_replay(db, event_id)
    db.refresh(contribution)
    db.refresh(event)
    assert first == {"selected": 1, "processed": 1, "orphaned": 0, "unresolved": 0}
    assert second["selected"] == 0
    assert event.resource_id == (order_id if event_type == "order" else payment_id)
    assert event.processed is True
    assert contribution.status == "PAID"
    assert db.query(PaymentSettlement).filter_by(payment_id=payment.id).count() == 1
    assert db.query(LedgerEntry).filter_by(reference_type="CONTRIBUTION_PAYMENT", reference_id=str(payment.id)).count() == 1
    assert db.query(Notification).filter_by(reference_type="CONTRIBUTION", reference_id=str(contribution.id)).count() == 1
    db.close()
    engine.dispose()


def test_same_event_same_resource_is_idempotent_then_replayable(tmp_path, monkeypatch):
    engine, Session = _database(tmp_path)
    db = Session()
    _valid_signature(monkeypatch)
    request = _Request("same-event", "payment-late", "payment")
    first = asyncio.run(api.mercado_pago_webhook(request, db))
    second = asyncio.run(api.mercado_pago_webhook(request, db))
    assert first["reconciliable"] is True and second["reconciliable"] is True
    assert db.query(WebhookEvent).filter_by(event_id="same-event").count() == 1
    event = db.query(WebhookEvent).filter_by(event_id="same-event").one()
    assert event.resource_id == "payment-late" and event.processed is False
    db.close()
    engine.dispose()


def test_same_event_conflicting_resource_fails_closed_without_provider_lookup(
    tmp_path, monkeypatch
):
    engine, Session = _database(tmp_path)
    db = Session()
    _valid_signature(monkeypatch)
    asyncio.run(api.mercado_pago_webhook(_Request("conflicting", "resource-original"), db))
    lookups = []

    async def forbidden_get_order(self, order_id):
        lookups.append(order_id)
        raise AssertionError("conflicting event resource must not be queried")

    monkeypatch.setattr(api.MercadoPagoClient, "get_order", forbidden_get_order)
    response = asyncio.run(
        api.mercado_pago_webhook(_Request("conflicting", "resource-conflict"), db)
    )
    event = db.query(WebhookEvent).filter_by(event_id="conflicting").one()
    assert response["inconsistent"] is True
    assert event.resource_id == "resource-original"
    assert event.processed is False
    assert lookups == []
    assert db.query(Payment).count() == 0
    assert db.query(PaymentSettlement).count() == 0
    db.close()
    engine.dispose()


def test_transient_get_order_failure_keeps_event_retryable(tmp_path, monkeypatch):
    engine, Session = _database(tmp_path)
    db = Session()
    payment = _provider_payment(db, "transient")
    _valid_signature(monkeypatch)

    async def fail(self, order_id):
        raise TimeoutError("synthetic provider timeout")

    monkeypatch.setattr(api.MercadoPagoClient, "get_order", fail)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(api.mercado_pago_webhook(_Request("transient-event", payment.provider_payment_id), db))
    assert exc_info.value.status_code == 502
    event = db.query(WebhookEvent).filter_by(event_id="transient-event").one()
    assert event.resource_id == payment.provider_payment_id
    assert event.processed is False
    db.close()
    engine.dispose()


def test_missing_provider_order_id_stays_unprocessed(tmp_path, monkeypatch):
    engine, Session = _database(tmp_path)
    db = Session()
    payment = Payment(
        provider="mercado_pago",
        provider_payment_id="payment-without-order",
        provider_order_id=None,
        idempotency_key="missing-order-key",
        amount=Decimal("10.00"),
        status="pending",
    )
    db.add(payment)
    db.commit()
    _valid_signature(monkeypatch)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(api.mercado_pago_webhook(_Request("missing-order-event", "payment-without-order"), db))
    assert exc_info.value.status_code == 502
    event = db.query(WebhookEvent).filter_by(event_id="missing-order-event").one()
    assert event.processed is False
    assert event.resource_id == "payment-without-order"
    assert db.query(PaymentSettlement).count() == 0
    db.close()
    engine.dispose()


def test_multiple_payment_correlations_fail_closed(tmp_path, monkeypatch):
    engine, Session = _database(tmp_path)
    db = Session()
    _provider_payment(db, "duplicate-a", order_id="shared-order")
    _provider_payment(db, "duplicate-b", order_id="shared-order")
    _valid_signature(monkeypatch)
    lookups = []

    async def forbidden_get_order(self, order_id):
        lookups.append(order_id)
        raise AssertionError("ambiguous local correlation must not query provider")

    monkeypatch.setattr(api.MercadoPagoClient, "get_order", forbidden_get_order)
    result = asyncio.run(api.mercado_pago_webhook(_Request("ambiguous-event", "shared-order", "order"), db))
    event = db.query(WebhookEvent).filter_by(event_id="ambiguous-event").one()
    assert result["reconciliable"] is True
    assert event.processed is False
    assert lookups == []
    assert db.query(PaymentSettlement).count() == 0
    assert db.query(LedgerEntry).count() == 0
    db.close()
    engine.dispose()


@pytest.mark.parametrize("remote_status", ["refunded", "charged_back"])
def test_replay_refund_and_chargeback_never_auto_reverse(tmp_path, monkeypatch, remote_status):
    engine, Session = _database(tmp_path)
    db = Session()
    _, contribution = _seed_contribution(db, remote_status)
    payment = _provider_payment(
        db,
        remote_status,
        reference_type="CONTRIBUTION",
        reference_id=contribution.id,
    )
    event = WebhookEvent(
        provider="mercado_pago",
        event_id=f"{remote_status}-event",
        event_type="payment",
        resource_id=payment.provider_payment_id,
        processed=False,
    )
    db.add(event)
    db.commit()

    async def get_order(self, order_id):
        return _remote(payment.provider_payment_id, remote_status)

    monkeypatch.setattr(api.MercadoPagoClient, "get_order", get_order)
    result = _admin_replay(db, event.event_id)
    db.refresh(payment)
    db.refresh(contribution)
    assert result["processed"] == 1
    assert payment.reconciliation_status == "RECONCILIATION_REQUIRED"
    assert contribution.status == "PENDING"
    assert db.query(PaymentSettlement).filter_by(payment_id=payment.id).count() == 0
    assert db.query(LedgerEntry).filter_by(reference_id=str(payment.id)).count() == 0
    assert db.query(PaymentReversal).filter_by(payment_id=payment.id).count() == 0
    db.close()
    engine.dispose()


def test_replay_settles_agreement_installment_and_is_idempotent(tmp_path, monkeypatch):
    engine, Session = _database(tmp_path)
    db = Session()
    member, agreement, installment = _seed_agreement(db, "agreement")
    payment = _provider_payment(
        db,
        "agreement",
        reference_type="AGREEMENT_INSTALLMENT",
        reference_id=installment.id,
        attempt_status="PENDING",
    )
    event = WebhookEvent(
        provider="mercado_pago", event_id="agreement-replay", event_type="payment",
        resource_id=payment.provider_payment_id, processed=False,
    )
    db.add(event)
    db.commit()

    async def get_order(self, order_id):
        return _remote(
            payment.provider_payment_id,
            "processed",
            status_detail="accredited",
            transaction_amount="10.00",
            date_approved="2026-01-01T12:00:00Z",
        )

    monkeypatch.setattr(api.MercadoPagoClient, "get_order", get_order)
    first = _admin_replay(db, event.event_id)
    db.refresh(agreement)
    revision = agreement.state_revision
    second = _admin_replay(db, event.event_id)
    db.refresh(installment)
    assert first["processed"] == 1 and second["selected"] == 0
    assert installment.status == "PAID"
    assert agreement.status == "SETTLED"
    assert agreement.state_revision == revision
    assert db.query(PaymentSettlement).filter_by(payment_id=payment.id).count() == 1
    assert db.query(LedgerEntry).filter_by(
        reference_type="AGREEMENT_INSTALLMENT_PAYMENT", reference_id=str(payment.id)
    ).count() == 1
    assert db.query(Notification).filter_by(
        reference_type="AGREEMENT_INSTALLMENT", reference_id=str(installment.id)
    ).count() == 1
    assert db.query(PaymentReversal).filter_by(payment_id=payment.id).count() == 0
    db.close()
    engine.dispose()


def test_replay_settles_versioned_loan_installment_once(tmp_path, monkeypatch):
    engine, Session = _database(tmp_path)
    db = Session()
    member, loan, installment, day, snapshot, confirmed_at = _seed_loan(db, "loan")
    payment = _provider_payment(
        db,
        "loan",
        reference_type="LOAN_INSTALLMENT",
        reference_id=installment.id,
        attempt_status="PENDING",
        loan_snapshot=(day, snapshot),
    )
    event = WebhookEvent(
        provider="mercado_pago", event_id="loan-replay", event_type="order",
        resource_id=payment.provider_order_id, processed=False,
    )
    db.add(event)
    db.commit()

    async def get_order(self, order_id):
        return _remote(
            payment.provider_payment_id,
            "approved",
            transaction_amount="10.00",
            date_approved=confirmed_at.isoformat(),
        )

    monkeypatch.setattr(api.MercadoPagoClient, "get_order", get_order)
    first = _admin_replay(db, event.event_id)
    second = _admin_replay(db, event.event_id)
    db.refresh(installment)
    db.refresh(loan)
    assert first["processed"] == 1 and second["selected"] == 0
    assert installment.status == "PAID"
    assert db.query(PaymentSettlement).filter_by(payment_id=payment.id).count() == 1
    assert db.query(LedgerEntry).filter_by(reference_id=str(payment.id)).count() >= 1
    assert db.query(Notification).filter_by(
        reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id)
    ).count() == 1
    assert db.query(PaymentReversal).filter_by(payment_id=payment.id).count() == 0
    db.close()
    engine.dispose()


def test_admin_replay_endpoint_rejects_non_admin(tmp_path):
    engine, Session = _database(tmp_path)
    previous_get_db = app.dependency_overrides.get(get_db)
    previous_current_user = app.dependency_overrides.get(current_user)

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(role="USER")
    try:
        with TestClient(app) as client:
            denied = client.post("/api/payments/webhook/mercado-pago/replay")
            assert denied.status_code == 403
            app.dependency_overrides[current_user] = lambda: SimpleNamespace(role="ADMIN")
            allowed = client.post("/api/payments/webhook/mercado-pago/replay")
            assert allowed.status_code == 200
            assert allowed.json() == {
                "selected": 0, "processed": 0, "orphaned": 0, "unresolved": 0
            }
    finally:
        if previous_get_db is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous_get_db
        if previous_current_user is None:
            app.dependency_overrides.pop(current_user, None)
        else:
            app.dependency_overrides[current_user] = previous_current_user
        engine.dispose()


def test_postgresql_duplicate_replay_serializes_on_persisted_event(monkeypatch):
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url.startswith("postgresql"):
        pytest.skip("requires the PostgreSQL CI service")
    engine = create_engine(database_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    provider_payment_id = f"f12f-pg-payment-{suffix}"
    provider_order_id = f"f12f-pg-order-{suffix}"
    event_id = f"f12f-pg-event-{suffix}"
    db = Session()
    payment = Payment(
        provider="mercado_pago", provider_order_id=provider_order_id,
        provider_payment_id=provider_payment_id, idempotency_key=f"f12f-pg-key-{suffix}",
        amount=Decimal("1.00"), status="pending",
    )
    db.add(payment)
    db.flush()
    event = WebhookEvent(
        provider="mercado_pago", event_id=event_id, event_type="payment",
        resource_id=provider_payment_id, processed=False,
    )
    db.add(event)
    db.commit()
    event_pk = event.id
    db.close()

    lock = threading.Lock()
    calls = []

    async def get_order(self, order_id):
        with lock:
            calls.append(order_id)
        time.sleep(0.1)
        return _remote(provider_payment_id, "pending")

    monkeypatch.setattr(api.MercadoPagoClient, "get_order", get_order)

    def replay_once():
        session = Session()
        try:
            return asyncio.run(api._process_mercado_pago_event(session, event_pk))
        finally:
            session.close()

    try:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: replay_once(), range(2)))
        check = Session()
        stored_event = check.query(WebhookEvent).filter_by(event_id=event_id).one()
        assert sorted(outcomes) == ["processed", "processed"]
        assert stored_event.processed is True
        assert calls == [provider_order_id]
        check.close()
    finally:
        cleanup = Session()
        cleanup.query(WebhookEvent).filter_by(event_id=event_id).delete()
        cleanup.query(Payment).filter_by(id=payment.id).delete()
        cleanup.commit()
        cleanup.close()
        engine.dispose()
