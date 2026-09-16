import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import (
    AuditLog,
    Contribution,
    LedgerEntry,
    MonthlyClosing,
    Payment,
    PaymentReversal,
    PaymentReversalComponent,
    PaymentSettlement,
    User,
)
from app.services import payment_reversal as reversal_service
from app.services.ledger import reverse_entry, verify_ledger_chain
from app.services.payment_reversal import reverse_payment
from app.services.payment_settlement import settle_confirmed_pix_payment


def _db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _setup(db, *, amount="100.00", received=None, due_date=None, suffix="case"):
    user = User(
        name=f"Master {suffix}",
        email=f"master-{suffix}@test",
        cpf=f"master-{suffix}",
        password_hash="x",
        role="ADMIN",
        is_active=True,
        is_master=True,
    )
    db.add(user)
    db.flush()
    contribution = Contribution(
        member_id=None,
        competence=date(2026, 1, 1),
        amount=Decimal(amount),
        due_date=due_date or date(2026, 12, 1),
        status="PENDING",
    )
    # The contribution's member is required by the real schema; use the
    # smallest valid member graph while keeping the reversal test focused.
    from app.models import Group, Member

    group_user = User(
        name=f"Member {suffix}",
        email=f"member-{suffix}@test",
        cpf=f"member-{suffix}",
        password_hash="x",
    )
    group = Group(name=f"Group {suffix}", max_installments=6)
    db.add_all([group_user, group])
    db.flush()
    member = Member(user_id=group_user.id, group_id=group.id)
    db.add(member)
    db.flush()
    contribution.member_id = member.id
    db.add(contribution)
    db.flush()
    payment = Payment(
        provider="mercado_pago",
        provider_payment_id=f"provider-{suffix}",
        idempotency_key=f"idempotency-{suffix}",
        amount=Decimal(amount),
        amount_received=None if received is None else Decimal(received),
        status="approved",
        raw_status="approved",
        reference_type="CONTRIBUTION",
        reference_id=str(contribution.id),
    )
    db.add(payment)
    db.flush()
    settlement = settle_confirmed_pix_payment(
        db,
        payment,
        confirmation_source="WEBHOOK",
        confirmed_at=datetime(2026, 9, 16, 3, 50, tzinfo=timezone.utc),
    )
    db.commit()
    return user, contribution, payment, settlement


def test_partial_contribution_reversal_compensates_ledger_receipt_and_audit():
    db = _db()
    admin, contribution, payment, settlement = _setup(db, received="40.00", suffix="partial")
    before_hash = settlement.receipt_hash
    original = db.query(LedgerEntry).one()
    settlement_before = {
        field: getattr(settlement, field)
        for field in (
            "receipt_number", "receipt_version", "receipt_snapshot_json", "receipt_hash",
            "amount_received", "amount_applied", "principal_applied", "interest_applied",
            "penalty_applied", "excess_amount",
        )
    }
    ledger_before = {
        field: getattr(original, field)
        for field in (
            "amount", "direction", "account", "reference_type", "reference_id",
            "reversal_of_id", "previous_hash", "entry_hash",
        )
    }
    now = datetime(2026, 9, 16, 3, 50, tzinfo=timezone.utc)

    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Correção manual", now=now)
    db.commit()

    assert contribution.paid_amount == Decimal("0.00")
    assert contribution.status == "PENDING"
    assert contribution.paid_at is None
    assert payment.status == "approved"
    assert payment.ledger_posted_at is not None
    assert settlement.receipt_hash == before_hash
    assert {field: getattr(settlement, field) for field in settlement_before} == settlement_before
    assert {field: getattr(original, field) for field in ledger_before} == ledger_before
    assert reversal.original_competence == contribution.competence
    assert reversal.original_due_date is None
    assert reversal.reversal_competence == date(2026, 9, 1)
    assert reversal.receipt_version == "v1"

    compensation = db.query(LedgerEntry).filter(LedgerEntry.reversal_of_id == original.id).one()
    component = db.query(PaymentReversalComponent).one()
    assert compensation.account == original.account == "CAIXINHA"
    assert compensation.amount == original.amount == Decimal("40.00")
    assert compensation.direction == "DEBIT"
    assert compensation.reference_type == "REVERSAL"
    assert compensation.reference_id == str(original.id)
    assert component.payment_reversal_id == reversal.id
    assert component.original_ledger_entry_id == original.id
    assert component.compensating_ledger_entry_id == compensation.id
    assert verify_ledger_chain(db)["status"] == "PASS"

    snapshot = json.loads(reversal.receipt_snapshot_json)
    assert snapshot["receipt_version"] == "v1"
    assert snapshot["receipt_number"] == reversal.receipt_number
    assert snapshot["reversal"]["id"] == reversal.id
    assert snapshot["payment"] == {"id": payment.id, "status": "approved"}
    assert snapshot["settlement"] == {
        "id": settlement.id,
        "receipt_number": settlement.receipt_number,
        "receipt_version": "v1",
    }
    assert snapshot["ledger_components"][0]["original_ledger_entry_id"] == original.id
    assert hashlib.sha256(reversal.receipt_snapshot_json.encode("utf-8")).hexdigest() == reversal.receipt_hash
    audit = db.query(AuditLog).filter_by(action="PAYMENT_REVERSED").one()
    assert json.loads(audit.details)["receipt_hash"] == reversal.receipt_hash
    db.close()


def test_full_paid_contribution_reversal_clears_paid_at_and_restores_pending():
    db = _db()
    admin, contribution, payment, _settlement = _setup(db, suffix="paid")
    assert contribution.status == "PAID"
    assert contribution.paid_at is not None
    reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Desfazer pagamento", now=datetime(2026, 9, 16, tzinfo=timezone.utc))
    db.commit()
    assert contribution.paid_amount == Decimal("0.00")
    assert contribution.status == "PENDING"
    assert contribution.paid_at is None
    db.close()


def test_overdue_contribution_reversal_derives_overdue_status():
    db = _db()
    admin, contribution, payment, _settlement = _setup(
        db,
        suffix="overdue",
        due_date=date(2026, 9, 1),
    )
    reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Desfazer atraso", now=datetime(2026, 9, 16, tzinfo=timezone.utc))
    db.commit()
    assert contribution.status == "OVERDUE"
    assert contribution.paid_amount == Decimal("0.00")
    assert contribution.paid_at is None
    db.close()


def test_retry_returns_same_reversal_without_new_effects():
    db = _db()
    admin, contribution, payment, _settlement = _setup(db, received="40.00", suffix="retry")
    first = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Primeiro estorno", now=datetime(2026, 9, 16, tzinfo=timezone.utc))
    db.commit()
    snapshot = first.receipt_snapshot_json
    receipt_hash = first.receipt_hash
    ledger_count = db.query(LedgerEntry).count()
    audit_count = db.query(AuditLog).count()
    second = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Outro motivo", now=datetime(2026, 10, 16, tzinfo=timezone.utc))
    assert second.id == first.id
    assert second.receipt_snapshot_json == snapshot
    assert second.receipt_hash == receipt_hash
    assert db.query(LedgerEntry).count() == ledger_count
    assert db.query(AuditLog).count() == audit_count
    assert contribution.paid_amount == Decimal("0.00")
    db.close()


def test_excess_is_copied_without_creating_a_fictitious_ledger_entry():
    db = _db()
    admin, contribution, payment, settlement = _setup(db, amount="100.00", received="130.00", suffix="excess")
    original_count = db.query(LedgerEntry).count()
    reversal = reverse_payment(
        db,
        payment_id=payment.id,
        admin_id=admin.id,
        reason="Estorno com excesso",
        now=datetime(2026, 9, 16, tzinfo=timezone.utc),
    )
    db.commit()
    assert settlement.amount_received == Decimal("130.00")
    assert settlement.amount_applied == Decimal("100.00")
    assert settlement.excess_amount == Decimal("30.00")
    assert reversal.amount_received == Decimal("130.00")
    assert reversal.amount_applied == Decimal("100.00")
    assert reversal.excess_amount == Decimal("30.00")
    assert reversal.amount_received == reversal.amount_applied + reversal.excess_amount
    assert contribution.paid_amount == Decimal("0.00")
    assert db.query(LedgerEntry).count() == original_count + 1
    assert db.query(LedgerEntry).filter(LedgerEntry.reversal_of_id.is_not(None)).one().amount == Decimal("100.00")
    snapshot = json.loads(reversal.receipt_snapshot_json)
    assert snapshot["amounts"] == {
        "amount_received": "130.00",
        "amount_applied": "100.00",
        "principal_applied": "100.00",
        "interest_applied": "0.00",
        "penalty_applied": "0.00",
        "excess_amount": "30.00",
    }
    retry = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Novo motivo")
    assert retry.id == reversal.id
    assert db.query(LedgerEntry).count() == original_count + 1
    db.close()


def test_reversal_is_allowed_without_modifying_a_closed_original_competence():
    db = _db()
    admin, contribution, payment, _settlement = _setup(db, suffix="closed")
    closing = MonthlyClosing(
        competence=contribution.competence,
        status="CLOSED",
        total_contributions=Decimal("100.00"),
        total_expenses=Decimal("2.00"),
        total_interest_received=Decimal("0.00"),
        ledger_balance=Decimal("98.00"),
        closed_by=admin.id,
        closed_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    db.add(closing)
    db.commit()
    before = {
        field: getattr(closing, field)
        for field in (
            "id", "competence", "status", "total_contributions", "total_expenses",
            "total_interest_received", "ledger_balance", "closed_by", "closed_at", "created_at",
        )
    }
    reversal = reverse_payment(
        db,
        payment_id=payment.id,
        admin_id=admin.id,
        reason="Estorno após fechamento",
        now=datetime(2026, 9, 16, 3, 50, tzinfo=timezone.utc),
    )
    db.commit()
    assert reversal.reversal_competence == date(2026, 9, 1)
    assert contribution.competence == date(2026, 1, 1)
    assert {field: getattr(closing, field) for field in before} == before
    db.close()


def test_incompatible_contribution_member_reference_fails_before_mutation():
    db = _db()
    admin, contribution, payment, settlement = _setup(db, suffix="member-mismatch")
    other = Contribution(
        member_id=contribution.member_id,
        competence=date(2026, 2, 1),
        amount=Decimal("100.00"),
        status="PENDING",
    )
    db.add(other)
    db.flush()
    db.execute(
        PaymentSettlement.__table__.update()
        .where(PaymentSettlement.id == settlement.id)
        .values(contribution_id=other.id)
    )
    db.commit()
    paid_before = contribution.paid_amount
    with pytest.raises(ValueError, match="Referência"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Membro incompatível")
    assert db.query(PaymentReversal).count() == 0
    assert contribution.paid_amount == paid_before
    assert db.query(LedgerEntry).filter(LedgerEntry.reference_type == "REVERSAL").count() == 0
    db.close()


@pytest.mark.parametrize("reason", ["", "  ", "abc"])
def test_invalid_reason_fails_before_mutation(reason):
    db = _db()
    _admin, _contribution, payment, _settlement = _setup(db, suffix=f"reason{len(reason)}")
    with pytest.raises(ValueError, match="motivo"):
        reverse_payment(db, payment_id=payment.id, admin_id=999999, reason=reason)
    assert db.query(PaymentReversal).count() == 0
    db.close()


def test_reversal_rejects_missing_settlement_and_unapproved_payment():
    db = _db()
    admin, _contribution, payment, settlement = _setup(db, suffix="invalid")
    db.delete(settlement)
    db.commit()
    with pytest.raises(ValueError, match="Settlement"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Sem settlement")
    payment.status = "pending"
    db.commit()
    with pytest.raises(ValueError, match="aprovados"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Não aprovado")
    db.close()


def test_reversal_rejects_unknown_payment_and_non_master_admin():
    db = _db()
    admin, _contribution, payment, _settlement = _setup(db, suffix="authorization")
    with pytest.raises(ValueError, match="não encontrado"):
        reverse_payment(db, payment_id=999999, admin_id=admin.id, reason="Pagamento ausente")
    admin.is_master = False
    db.commit()
    with pytest.raises(ValueError, match="Master"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Admin inválido")
    assert db.query(PaymentReversal).count() == 0
    db.close()


def test_reversal_rejects_incompatible_settlement():
    db = _db()
    admin, _contribution, payment, settlement = _setup(db, suffix="settlement-type")
    payment.reference_type = "LOAN_INSTALLMENT"
    db.commit()
    with pytest.raises(ValueError, match="contribuição|incompatível"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Tipo inválido")
    assert db.query(PaymentReversal).count() == 0
    db.close()


def test_reversal_rejects_insufficient_paid_amount_and_invalid_settlement_components():
    db = _db()
    admin, contribution, payment, settlement = _setup(db, received="40.00", suffix="insufficient")
    contribution.paid_amount = Decimal("10.00")
    db.commit()
    with pytest.raises(ValueError, match="suficiente"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Valor insuficiente")
    settlement.interest_applied = Decimal("1.00")
    with pytest.raises(IntegrityError, match="ck_payment_settlements_applied_components"):
        db.commit()
    db.rollback()
    db.close()


def test_reversal_rejects_missing_or_incompatible_ledger():
    db = _db()
    admin, _contribution, payment, _settlement = _setup(db, received="40.00", suffix="ledger")
    original = db.query(LedgerEntry).one()
    db.execute(delete(LedgerEntry).where(LedgerEntry.id == original.id))
    db.commit()
    with pytest.raises(ValueError, match="Ledger"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Ledger ausente")
    db.close()


def test_reversal_rejects_duplicate_or_wrong_ledger_component():
    db = _db()
    admin, _contribution, payment, _settlement = _setup(db, received="40.00", suffix="ledger-extra")
    original = db.query(LedgerEntry).one()
    db.add(
        LedgerEntry(
            account="CAIXINHA",
            direction="CREDIT",
            amount=Decimal("40.00"),
            reference_type="CONTRIBUTION_PAYMENT",
            reference_id=str(payment.id),
        )
    )
    db.commit()
    with pytest.raises(ValueError, match="duplicado"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Ledger duplicado")
    assert db.query(PaymentReversal).count() == 0
    db.close()

    db = _db()
    admin, _contribution, payment, _settlement = _setup(db, received="40.00", suffix="ledger-direction")
    original = db.query(LedgerEntry).one()
    db.execute(LedgerEntry.__table__.update().where(LedgerEntry.id == original.id).values(direction="DEBIT"))
    db.commit()
    with pytest.raises(ValueError, match="incompatível"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Direção inválida")
    db.close()


def test_reversal_rejects_existing_generic_ledger_reversal():
    db = _db()
    admin, _contribution, payment, _settlement = _setup(db, received="40.00", suffix="generic")
    original = db.query(LedgerEntry).one()
    reverse_entry(db, original, "Reversão histórica")
    db.commit()
    with pytest.raises(ValueError, match="já possui reversão"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Bloquear duplicidade")
    assert db.query(PaymentReversal).count() == 0
    db.close()


def test_reversal_rolls_back_reversal_ledger_contribution_and_audit(monkeypatch):
    db = _db()
    admin, contribution, payment, settlement = _setup(db, received="40.00", suffix="rollback")
    before_paid = contribution.paid_amount
    before_status = contribution.status
    before_settlement_hash = settlement.receipt_hash
    before_payment_ledger_at = payment.ledger_posted_at

    def fail_after_mutation(**_kwargs):
        raise RuntimeError("falha controlada após mutações")

    monkeypatch.setattr(reversal_service, "_receipt_snapshot", fail_after_mutation)
    with pytest.raises(RuntimeError, match="após mutações"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Forçar rollback")
    db.rollback()

    db.expire_all()
    assert db.query(PaymentReversal).count() == 0
    assert db.query(PaymentReversalComponent).count() == 0
    assert db.query(LedgerEntry).filter(LedgerEntry.reference_type == "REVERSAL").count() == 0
    assert db.query(AuditLog).filter(AuditLog.action == "PAYMENT_REVERSED").count() == 0
    assert contribution.paid_amount == before_paid
    assert contribution.status == before_status
    assert settlement.receipt_hash == before_settlement_hash
    assert payment.ledger_posted_at == before_payment_ledger_at
    db.close()


def test_reversal_rolls_back_after_auditlog_flush(monkeypatch):
    db = _db()
    admin, contribution, payment, settlement = _setup(db, received="40.00", suffix="rollback-audit")
    settlement_before = {
        field: getattr(settlement, field)
        for field in ("receipt_number", "receipt_version", "receipt_snapshot_json", "receipt_hash", "amount_applied")
    }
    ledger_before = {
        field: getattr(db.query(LedgerEntry).one(), field)
        for field in ("amount", "direction", "account", "reference_type", "reference_id", "reversal_of_id", "previous_hash", "entry_hash")
    }
    paid_before = contribution.paid_amount
    status_before = contribution.status
    ledger_posted_before = payment.ledger_posted_at
    real_flush = db.flush

    def flush_then_fail(*args, **kwargs):
        audit_pending = any(isinstance(item, AuditLog) for item in db.new)
        real_flush(*args, **kwargs)
        if audit_pending:
            assert db.query(AuditLog).filter_by(action="PAYMENT_REVERSED").count() == 1
            raise RuntimeError("falha controlada após AuditLog")

    monkeypatch.setattr(db, "flush", flush_then_fail)
    with pytest.raises(RuntimeError, match="após AuditLog"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Rollback audit")
    db.rollback()
    db.expire_all()

    assert db.query(PaymentReversal).count() == 0
    assert db.query(PaymentReversalComponent).count() == 0
    assert db.query(LedgerEntry).filter(LedgerEntry.reference_type == "REVERSAL").count() == 0
    assert db.query(AuditLog).filter_by(action="PAYMENT_REVERSED").count() == 0
    assert contribution.paid_amount == paid_before
    assert contribution.status == status_before
    assert payment.status == "approved"
    assert payment.ledger_posted_at == ledger_posted_before
    assert {field: getattr(settlement, field) for field in settlement_before} == settlement_before
    original = db.query(LedgerEntry).one()
    assert {field: getattr(original, field) for field in ledger_before} == ledger_before
    db.close()
