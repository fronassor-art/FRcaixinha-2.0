import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, update

from app.models import AuditLog, LedgerEntry, MemberFinancialEntry, MonthlyClosing, PaymentReversal, User
from app.services import payment_reversal as reversal_service
from app.services.ledger import post_entry, reverse_entry, verify_ledger_chain
from app.services.member_financial import get_member_financial_position
from app.services.payment_reversal import reverse_payment
from app.services.payment_settlement import settle_confirmed_pix_payment
from test_payment_settlement_v103 import _db, _installment, _member, _payment, _settle


def _admin(db, suffix):
    row = User(name=f"Master {suffix}", email=f"master-{suffix}@test", cpf=f"master-{suffix}", password_hash="x", role="ADMIN", is_active=True, is_master=True)
    db.add(row)
    db.flush()
    return row


def _loan_payment(db, suffix, amount="120.00", interest="20.00", penalty="0.00"):
    member = _member(db, suffix)
    loan, installment = _installment(db, member, amount=amount, interest=interest, penalty=penalty)
    payment = _payment(db, suffix=suffix, amount=amount, reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id))
    settlement = _settle(db, payment)
    admin = _admin(db, suffix)
    db.commit()
    return admin, member, loan, installment, payment, settlement


def test_loan_reversal_restores_literal_state_and_compensates_all_components():
    db = _db()
    admin, member, loan, installment, payment, settlement = _loan_payment(db, "a2-full", penalty="10.00", amount="130.00")
    settlement_snapshot = settlement.receipt_snapshot_json
    settlement_hash = settlement.receipt_hash
    original_mfe = db.query(MemberFinancialEntry).filter_by(entry_type="LOAN_PRINCIPAL_PAYMENT").one()
    originals = db.query(LedgerEntry).filter(LedgerEntry.reference_id == str(payment.id)).all()
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno A2 integral", now=datetime(2026, 9, 16, tzinfo=timezone.utc))
    db.commit()
    assert payment.status == "approved"
    assert loan.status == "ACTIVE" and loan.paid_at is None and loan.state_revision == 2
    assert installment.status == "OPEN" and installment.paid_amount == Decimal("0.00") and installment.paid_penalty_amount == Decimal("0.00") and installment.paid_at is None
    compensation = db.query(MemberFinancialEntry).filter_by(payment_reversal_id=reversal.id).one()
    assert compensation.entry_type == "LOAN_PRINCIPAL_REVERSAL" and compensation.direction == "DEBIT" and compensation.amount == Decimal("100.00")
    assert get_member_financial_position(db, member)["own_balance"] == Decimal("0.00")
    assert len(db.query(LedgerEntry).filter(LedgerEntry.reversal_of_id.in_([row.id for row in originals])).all()) == 2
    assert settlement.receipt_snapshot_json == settlement_snapshot and settlement.receipt_hash == settlement_hash
    snapshot = json.loads(reversal.receipt_snapshot_json)
    assert snapshot["loan_installment"]["status_before"] == "OPEN"
    assert hashlib.sha256(reversal.receipt_snapshot_json.encode()).hexdigest() == reversal.receipt_hash
    assert verify_ledger_chain(db)["status"] == "PASS"


def test_loan_reversal_requires_v4_and_master_and_is_idempotent():
    db = _db()
    admin, _, _, _, payment, settlement = _loan_payment(db, "a2-idempotent")
    assert reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Primeiro estorno").id
    first = db.query(PaymentReversal).one()
    count = (db.query(MemberFinancialEntry).count(), db.query(LedgerEntry).count(), db.query(AuditLog).count())
    assert reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Outro motivo").id == first.id
    assert (db.query(MemberFinancialEntry).count(), db.query(LedgerEntry).count(), db.query(AuditLog).count()) == count
    db.rollback()
    legacy_db = _db()
    legacy_admin, _, _, _, legacy_payment, legacy_settlement = _loan_payment(legacy_db, "a2-legacy")
    legacy_settlement.receipt_version = "v3"
    with pytest.raises(ValueError, match="v4"):
        with legacy_db.no_autoflush:
            reverse_payment(legacy_db, payment_id=legacy_payment.id, admin_id=legacy_admin.id, reason="Versão antiga")
    db.rollback()


def test_loan_reversal_blocks_insufficient_own_balance_without_partial_effects():
    db = _db()
    admin, member, loan, installment, payment, settlement = _loan_payment(db, "a2-insufficient")
    account = member.financial_account
    db.add(MemberFinancialEntry(account_id=account.id, entry_type="OWN_BALANCE_SETTLEMENT", direction="DEBIT", amount=Decimal("1.00"), reference_type="TEST", reference_id="1"))
    db.commit()
    with pytest.raises(ValueError, match="insuficiente"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Saldo insuficiente")
    db.rollback()
    assert db.query(PaymentReversal).count() == 0 and loan.status == "PAID" and installment.status == "PAID"


def test_loan_reversal_blocks_current_installment_or_loan_mismatch():
    db = _db()
    admin, _, loan, installment, payment, _ = _loan_payment(db, "a2-mismatch")
    installment.status = "PARTIAL"
    db.commit()
    with pytest.raises(ValueError, match="parcela"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estado divergente")
    db.rollback()
    installment.status = "PAID"
    loan.state_revision += 1
    db.commit()
    with pytest.raises(ValueError, match="Loan"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Revision divergente")


def test_loan_reversal_requires_exact_original_mfe_and_ledger():
    db = _db()
    admin, _, _, _, payment, _ = _loan_payment(db, "a2-evidence")
    original = db.query(MemberFinancialEntry).filter_by(entry_type="LOAN_PRINCIPAL_PAYMENT").one()
    original.amount = Decimal("99.00")
    db.commit()
    with pytest.raises(ValueError, match="MFE"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="MFE incompatível")


def test_loan_reversal_with_zero_principal_has_only_ledger_compensation():
    db = _db()
    member = _member(db, "a2-zero-principal")
    loan, installment = _installment(db, member, amount="20.00", interest="20.00")
    payment = _payment(db, suffix="a2-zero-principal", amount="20.00", reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id))
    settlement = _settle(db, payment)
    admin = _admin(db, "a2-zero-principal")
    db.commit()
    assert settlement.principal_applied == Decimal("0.00")
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno sem principal")
    db.commit()
    assert db.query(MemberFinancialEntry).count() == 0
    assert db.query(PaymentReversal).one().id == reversal.id


def test_payment_linked_loan_ledger_cannot_use_generic_reverse_entry():
    db = _db()
    _, _, _, _, payment, _ = _loan_payment(db, "a2-guard")
    original = db.query(LedgerEntry).filter_by(reference_type="LOAN_INTEREST_PAYMENT").one()
    with pytest.raises(ValueError, match="PaymentReversal"):
        reverse_entry(db, original, "Estorno isolado")


def test_generic_ledger_without_payment_settlement_keeps_historical_reverse_entry():
    db = _db()
    original = post_entry(db, "CAIXINHA", "CREDIT", Decimal("3.00"), "MANUAL_ADJUSTMENT", "manual-1")
    db.flush()
    compensation = reverse_entry(db, original, "Ajuste manual válido")
    db.commit()
    assert compensation.reversal_of_id == original.id and compensation.direction == "DEBIT"


@pytest.mark.parametrize("field,value", [
    ("principal_applied", Decimal("99.00")),
    ("interest_applied", Decimal("19.00")),
    ("amount_applied", Decimal("119.00")),
    ("loan_status_after", "ACTIVE"),
    ("loan_state_revision_after", 99),
    ("loan_installment_status_after", "OPEN"),
    ("loan_paid_at_after", None),
])
def test_settlement_column_tampering_is_rejected_against_unchanged_snapshot(field, value):
    db = _db()
    admin, _, _, _, payment, settlement = _loan_payment(db, f"a2-tamper-{field}")
    setattr(settlement, field, value)
    with pytest.raises(ValueError):
        with db.no_autoflush:
            reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Coluna adulterada")
    db.rollback()
    assert db.query(PaymentReversal).count() == 0


def test_zero_principal_with_unexpected_principal_mfe_is_rejected():
    db = _db()
    member = _member(db, "a2-zero-mfe")
    _, installment = _installment(db, member, amount="20.00", interest="20.00")
    payment = _payment(db, suffix="a2-zero-mfe", amount="20.00", reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id))
    _settle(db, payment)
    admin = _admin(db, "a2-zero-mfe")
    account = member.financial_account
    db.add(MemberFinancialEntry(account_id=account.id, entry_type="LOAN_PRINCIPAL_PAYMENT", direction="CREDIT", amount=Decimal("1.00"), reference_type="LOAN_PRINCIPAL_PAYMENT", reference_id=str(payment.id)))
    db.commit()
    with pytest.raises(ValueError, match="inesperada"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="MFE inesperada")


def test_real_posterior_installment_payment_blocks_reversal_of_first_payment():
    db = _db()
    member = _member(db, "a2-posterior")
    loan, installment = _installment(db, member, amount="120.00", interest="20.00")
    payment_a = _payment(db, suffix="a2-posterior-a", amount="50.00", reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id))
    settlement_a = _settle(db, payment_a)
    payment_b = _payment(db, suffix="a2-posterior-b", amount="30.00", reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id))
    settlement_b = _settle(db, payment_b)
    admin = _admin(db, "a2-posterior")
    db.commit()
    assert settlement_a.loan_state_revision_after == 1
    assert settlement_b.loan_state_revision_after == 2
    before = (installment.paid_amount, installment.paid_penalty_amount, loan.status, loan.state_revision)
    with pytest.raises(ValueError, match="Loan"):
        reverse_payment(db, payment_id=payment_a.id, admin_id=admin.id, reason="Reverter primeiro")
    db.rollback()
    assert (installment.paid_amount, installment.paid_penalty_amount, loan.status, loan.state_revision) == before
    assert db.query(PaymentReversal).count() == 0


def test_missing_duplicate_and_mismatched_interest_ledger_are_rejected():
    for mode in ("missing", "duplicate", "mismatch"):
        db = _db()
        admin, _, _, _, payment, _ = _loan_payment(db, f"a2-ledger-{mode}")
        original = db.query(LedgerEntry).filter_by(reference_type="LOAN_INTEREST_PAYMENT").one()
        if mode == "missing":
            db.execute(delete(LedgerEntry).where(LedgerEntry.id == original.id))
        elif mode == "duplicate":
            db.add(LedgerEntry(account="CAIXINHA", direction="CREDIT", amount=Decimal("20.00"), reference_type="LOAN_INTEREST_PAYMENT", reference_id=str(payment.id)))
        else:
            db.execute(update(LedgerEntry).where(LedgerEntry.id == original.id).values(amount=Decimal("19.00")))
        db.commit()
        with pytest.raises(ValueError, match="Ledger"):
            reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Ledger inválido")


def test_atomic_failure_after_obligation_mutation_rolls_back_everything(monkeypatch):
    db = _db()
    admin, _, loan, installment, payment, settlement = _loan_payment(db, "a2-late-failure")
    original_settlement = (settlement.receipt_snapshot_json, settlement.receipt_hash)
    original_loan = (loan.status, loan.paid_at, loan.state_revision)
    original_installment = (installment.status, installment.paid_at, installment.paid_amount, installment.paid_penalty_amount)
    def fail(*args, **kwargs):
        raise RuntimeError("falha depois da obrigação")
    monkeypatch.setattr(reversal_service, "_loan_reversal_snapshot", fail)
    with pytest.raises(RuntimeError, match="depois"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Falha tardia real")
    db.rollback()
    assert db.query(PaymentReversal).count() == 0
    assert db.query(MemberFinancialEntry).filter_by(entry_type="LOAN_PRINCIPAL_REVERSAL").count() == 0
    assert db.query(LedgerEntry).filter(LedgerEntry.reversal_of_id.is_not(None)).count() == 0
    assert (loan.status, loan.paid_at, loan.state_revision) == original_loan
    assert (installment.status, installment.paid_at, installment.paid_amount, installment.paid_penalty_amount) == original_installment
    assert (settlement.receipt_snapshot_json, settlement.receipt_hash) == original_settlement
    assert payment.status == "approved"


def test_closed_original_due_period_is_not_reopened():
    db = _db()
    admin, _, _, installment, payment, _ = _loan_payment(db, "a2-closed")
    closing = MonthlyClosing(competence=installment.due_date, status="CLOSED")
    db.add(closing)
    db.commit()
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno competência fechada")
    db.commit()
    assert reversal.original_date_kind == "LOAN_INSTALLMENT_DUE_DATE"
    assert reversal.original_due_date == installment.due_date
    assert reversal.original_competence is None
    assert reversal.reversal_competence == date(reversal.reversed_at.year, reversal.reversed_at.month, 1)
    assert db.query(MonthlyClosing).one().status == "CLOSED"


def test_persisted_non_master_is_rejected_for_loan_reversal():
    db = _db()
    _, _, _, _, payment, _ = _loan_payment(db, "a2-nonmaster")
    non_master = User(name="Admin", email="nonmaster@test", cpf="nonmaster", password_hash="x", role="ADMIN", is_active=True, is_master=False)
    db.add(non_master)
    db.commit()
    with pytest.raises(ValueError, match="Master"):
        reverse_payment(db, payment_id=payment.id, admin_id=non_master.id, reason="Não autorizado")


def test_late_failure_rolls_back_reversal_and_compensations(monkeypatch):
    db = _db()
    admin, _, loan, installment, payment, _ = _loan_payment(db, "a2-atomic")
    before = (db.query(PaymentReversal).count(), db.query(MemberFinancialEntry).count(), db.query(LedgerEntry).count())
    def fail(*args, **kwargs):
        raise RuntimeError("falha tardia")
    monkeypatch.setattr(reversal_service, "post_entry", fail)
    with pytest.raises(RuntimeError, match="tardia"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Falha tardia")
    db.rollback()
    assert (db.query(PaymentReversal).count(), db.query(MemberFinancialEntry).count(), db.query(LedgerEntry).count()) == before
    assert loan.status == "PAID" and installment.status == "PAID"
