import hashlib
import json
import pytest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import Contribution, Group, LedgerEntry, Loan, LoanInstallment, Member, MemberFinancialEntry, Payment, PaymentSettlement, User
from app.services.ledger import verify_ledger_chain
from app.services import payment_settlement as payment_settlement_service
from app.services.payment_settlement import settle_confirmed_pix_payment


def _db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _member(db, suffix="1"):
    group = Group(name=f"Grupo {suffix}", max_installments=6)
    user = User(name=f"Membro {suffix}", email=f"member-{suffix}@test", cpf=f"cpf-{suffix}", password_hash="x")
    db.add_all([group, user])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id)
    db.add(member)
    db.flush()
    return member


def _payment(db, *, suffix, amount, reference_type=None, reference_id=None, received=None):
    payment = Payment(
        provider="mercado_pago",
        provider_payment_id=f"payment-{suffix}",
        idempotency_key=f"idempotency-{suffix}",
        amount=Decimal(amount),
        amount_received=None if received is None else Decimal(received),
        status="approved",
        raw_status="approved",
        reference_type=reference_type,
        reference_id=reference_id,
    )
    db.add(payment)
    db.flush()
    return payment


def _contribution(db, member, *, amount="100.00", due_date=None, status="PENDING"):
    row = Contribution(member_id=member.id, competence=date(2026, 1, 1), amount=Decimal(amount), due_date=due_date, status=status)
    db.add(row)
    db.flush()
    return row


def _installment(db, member, *, amount="120.00", interest="20.00", penalty="0.00", due_date=None):
    loan = Loan(member_id=member.id, principal=Decimal("100.00"), monthly_rate=Decimal("0.20"), installments=1, status="ACTIVE")
    db.add(loan)
    db.flush()
    row = LoanInstallment(
        loan_id=loan.id,
        number=1,
        due_date=due_date or date.today() + timedelta(days=10),
        principal=Decimal("100.00"),
        interest=Decimal(interest),
        amount=Decimal(amount),
        penalty_amount=Decimal(penalty),
        status="OPEN",
    )
    db.add(row)
    db.flush()
    return loan, row


def _settle(db, payment, when=None, **kwargs):
    result = settle_confirmed_pix_payment(
        db, payment, confirmation_source="WEBHOOK", confirmed_at=when or datetime.now(timezone.utc), **kwargs
    )
    db.commit()
    return result


def _utc_iso(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def test_full_contribution_uses_canonical_reference_and_posts_hashed_ledger():
    db = _db()
    member = _member(db, "full")
    contribution = _contribution(db, member, due_date=date.today() + timedelta(days=1))
    payment = _payment(db, suffix="full", amount="100.00", reference_type="CONTRIBUTION", reference_id=str(contribution.id))

    settlement = _settle(db, payment, remote_payload={"external_reference": "contribution-1", "access_token": "must-not-persist"})

    assert contribution.status == "PAID"
    assert contribution.paid_amount == Decimal("100.00")
    assert settlement.obligation_type == "CONTRIBUTION"
    assert settlement.receipt_version == "v1"
    assert settlement.loan_status_before is None
    assert settlement.loan_status_after is None
    assert settlement.loan_state_revision_before is None
    assert settlement.loan_state_revision_after is None
    assert "loan_state" not in json.loads(settlement.receipt_snapshot_json)
    assert settlement.principal_applied == Decimal("100.00")
    assert settlement.interest_applied == Decimal("0.00")
    assert settlement.excess_amount == Decimal("0.00")
    assert payment.external_reference == "contribution-1"
    assert "must-not-persist" not in payment.provider_payload_json
    assert "[REDACTED]" in payment.provider_payload_json
    assert verify_ledger_chain(db)["status"] == "PASS"
    assert db.query(LedgerEntry).count() == 1
    db.close()


def test_partial_contribution_keeps_partial_status_and_persists_received_amount():
    db = _db()
    member = _member(db, "partial")
    contribution = _contribution(db, member)
    payment = _payment(db, suffix="partial", amount="100.00", received="40.00", reference_type="CONTRIBUTION", reference_id=str(contribution.id))

    settlement = _settle(db, payment)

    assert contribution.status == "PARTIAL"
    assert contribution.paid_amount == Decimal("40.00")
    assert settlement.amount_received == Decimal("40.00")
    assert settlement.amount_applied == Decimal("40.00")
    assert settlement.obligation_status_after == "PARTIAL"
    db.close()


def test_overdue_contribution_is_determined_from_due_date_and_quits_to_paid():
    db = _db()
    member = _member(db, "overdue")
    contribution = _contribution(db, member, due_date=date.today() - timedelta(days=1))
    payment = _payment(db, suffix="overdue", amount="100.00", reference_type="CONTRIBUTION", reference_id=str(contribution.id))

    settlement = _settle(db, payment)

    assert settlement.obligation_status_before == "OVERDUE"
    assert settlement.obligation_status_after == "PAID"
    assert settlement.receipt_version == "v1"
    assert settlement.loan_status_before is None
    assert settlement.loan_status_after is None
    assert settlement.loan_state_revision_before is None
    assert settlement.loan_state_revision_after is None
    assert contribution.status == "PAID"
    assert contribution.paid_at is not None
    db.close()


def test_legacy_contribution_payment_link_remains_supported():
    db = _db()
    member = _member(db, "legacy")
    contribution = _contribution(db, member)
    payment = _payment(db, suffix="legacy", amount="100.00")
    contribution.payment_id = payment.id
    db.commit()

    settlement = _settle(db, payment)

    assert settlement.contribution_id == contribution.id
    assert settlement.obligation_type == "CONTRIBUTION"
    assert contribution.status == "PAID"
    db.close()


def test_full_installment_closes_installment_and_loan():
    db = _db()
    member = _member(db, "loan-full")
    loan, installment = _installment(db, member)
    payment = _payment(db, suffix="loan-full", amount="120.00", reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id))

    settlement = _settle(db, payment)

    assert installment.status == "PAID"
    assert loan.status == "PAID"
    assert settlement.interest_applied == Decimal("20.00")
    assert settlement.principal_applied == Decimal("100.00")
    assert settlement.penalty_applied == Decimal("0.00")
    assert settlement.receipt_version == "v4"
    assert settlement.loan_status_before == "ACTIVE"
    assert settlement.loan_status_after == "PAID"
    assert settlement.loan_state_revision_before == 0
    assert settlement.loan_state_revision_after == 1
    assert settlement.loan_paid_at_before is None
    assert settlement.loan_paid_at_after is not None
    snapshot = json.loads(settlement.receipt_snapshot_json)
    assert snapshot["loan_state"] == {
        "status_before": "ACTIVE",
        "status_after": "PAID",
        "state_revision_before": 0,
        "state_revision_after": 1,
        "paid_at_before": None,
        "paid_at_after": _utc_iso(settlement.loan_paid_at_after),
    }
    assert hashlib.sha256(settlement.receipt_snapshot_json.encode("utf-8")).hexdigest() == settlement.receipt_hash
    assert verify_ledger_chain(db)["status"] == "PASS"
    db.close()


def test_partial_installment_applies_penalty_then_interest_then_principal():
    db = _db()
    member = _member(db, "loan-partial")
    _, installment = _installment(db, member, penalty="10.00")
    payment = _payment(db, suffix="loan-partial", amount="50.00", reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id))

    settlement = _settle(db, payment)

    assert installment.status == "PARTIAL"
    assert installment.paid_penalty_amount == Decimal("10.00")
    assert installment.paid_amount == Decimal("40.00")
    assert settlement.penalty_applied == Decimal("10.00")
    assert settlement.interest_applied == Decimal("20.00")
    assert settlement.principal_applied == Decimal("20.00")
    assert settlement.receipt_version == "v4"
    assert settlement.loan_status_before == "ACTIVE"
    assert settlement.loan_status_after == "ACTIVE"
    assert settlement.loan_state_revision_before == 0
    assert settlement.loan_state_revision_after == 1
    assert settlement.loan_paid_at_before is None
    assert settlement.loan_paid_at_after is None
    assert [entry.reference_type for entry in db.query(LedgerEntry).order_by(LedgerEntry.id)] == ["LOAN_PENALTY_PAYMENT", "LOAN_INTEREST_PAYMENT"]
    assert verify_ledger_chain(db)["status"] == "PASS"
    db.close()


def test_loan_settlement_retry_returns_immutable_v2_evidence_without_new_revision():
    db = _db()
    member = _member(db, "loan-retry")
    loan, installment = _installment(db, member)
    payment = _payment(db, suffix="loan-retry", amount="50.00", reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id))
    first = _settle(db, payment)
    snapshot = first.receipt_snapshot_json
    receipt_hash = first.receipt_hash
    revision = loan.state_revision
    second = _settle(db, payment, remote_payload={"status_detail": "ignored"})
    assert second.id == first.id
    assert second.receipt_version == "v4"
    assert second.receipt_snapshot_json == snapshot
    assert second.receipt_hash == receipt_hash
    assert loan.state_revision == revision == 1
    db.close()


def test_loan_v2_settlement_rolls_back_all_mutations_when_snapshot_finalization_fails(monkeypatch):
    db = _db()
    member = _member(db, "loan-rollback-v2")
    loan, installment = _installment(db, member)
    payment = _payment(
        db,
        suffix="loan-rollback-v2",
        amount="120.00",
        reference_type="LOAN_INSTALLMENT",
        reference_id=str(installment.id),
    )
    db.commit()

    before = {
        "revision": loan.state_revision,
        "status": loan.status,
        "paid_at": loan.paid_at,
        "installment_status": installment.status,
        "paid_amount": installment.paid_amount,
        "paid_penalty_amount": installment.paid_penalty_amount,
        "ledger_posted_at": payment.ledger_posted_at,
    }

    def fail_snapshot(**kwargs):
        raise RuntimeError("falha controlada na finalização do receipt v2")

    monkeypatch.setattr(payment_settlement_service, "_receipt_snapshot", fail_snapshot)
    with pytest.raises(RuntimeError, match="finalização"):
        settle_confirmed_pix_payment(
            db,
            payment,
            confirmation_source="WEBHOOK",
            confirmed_at=datetime.now(timezone.utc),
        )
    db.rollback()

    db.refresh(loan)
    db.refresh(installment)
    db.refresh(payment)
    assert db.query(PaymentSettlement).filter_by(payment_id=payment.id).count() == 0
    assert loan.state_revision == before["revision"]
    assert loan.status == before["status"]
    assert loan.paid_at == before["paid_at"]
    assert installment.status == before["installment_status"]
    assert installment.paid_amount == before["paid_amount"]
    assert installment.paid_penalty_amount == before["paid_penalty_amount"]
    assert db.query(LedgerEntry).filter_by(reference_id=str(payment.id)).count() == 0
    assert db.query(MemberFinancialEntry).filter_by(reference_id=str(payment.id)).count() == 0
    assert payment.ledger_posted_at == before["ledger_posted_at"]
    db.close()


def test_excess_is_persisted_without_crediting_member_balance():
    db = _db()
    member = _member(db, "excess")
    _, installment = _installment(db, member)
    payment = _payment(db, suffix="excess", amount="150.00", reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id))

    settlement = _settle(db, payment)

    principal_entries = db.query(MemberFinancialEntry).filter(MemberFinancialEntry.entry_type == "LOAN_PRINCIPAL_PAYMENT").all()
    assert settlement.amount_applied == Decimal("120.00")
    assert settlement.excess_amount == Decimal("30.00")
    assert sum(Decimal(entry.amount) for entry in principal_entries) == Decimal("100.00")
    db.close()


def test_repeated_payment_returns_immutable_receipt_without_duplicate_ledger_or_settlement():
    db = _db()
    member = _member(db, "idem")
    contribution = _contribution(db, member)
    payment = _payment(db, suffix="idem", amount="100.00", reference_type="CONTRIBUTION", reference_id=str(contribution.id))

    first = _settle(db, payment)
    ledger_count = db.query(LedgerEntry).count()
    original_snapshot = first.receipt_snapshot_json
    second = _settle(db, payment, remote_payload={"status_detail": "ignored-on-repeat"})

    assert second.id == first.id
    assert second.receipt_number == first.receipt_number
    assert second.receipt_snapshot_json == original_snapshot
    assert hashlib.sha256(second.receipt_snapshot_json.encode("utf-8")).hexdigest() == second.receipt_hash
    assert json.dumps(json.loads(second.receipt_snapshot_json), sort_keys=True, separators=(",", ":"), ensure_ascii=False) == second.receipt_snapshot_json
    assert db.query(PaymentSettlement).filter(PaymentSettlement.payment_id == payment.id).count() == 1
    assert db.query(LedgerEntry).count() == ledger_count
    assert verify_ledger_chain(db)["status"] == "PASS"
    db.close()


def test_partial_payment_after_due_date_remains_overdue_with_outstanding_balance():
    db = _db()
    member = _member(db, "overdue-partial")
    contribution = _contribution(db, member, due_date=date.today() - timedelta(days=1))
    payment = _payment(db, suffix="overdue-partial", amount="100.00", received="40.00", reference_type="CONTRIBUTION", reference_id=str(contribution.id))

    settlement = _settle(db, payment)

    assert contribution.paid_amount == Decimal("40.00")
    assert contribution.status == "OVERDUE"
    assert settlement.obligation_status_before == "OVERDUE"
    assert settlement.obligation_status_after == "OVERDUE"
    db.close()
