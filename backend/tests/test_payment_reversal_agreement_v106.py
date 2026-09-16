from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models import AuditLog, LedgerEntry, MemberFinancialEntry, MonthlyClosing, PaymentReversal, PaymentReversalComponent, User
from app.services import payment_reversal as reversal_service
from app.services.ledger import post_entry, reverse_entry, verify_ledger_chain
from app.services.payment_reversal import reverse_payment
from app.services.reconciliation_v040 import build_advanced_reconciliation
from test_agreement_payment_settlement_v104 import _agreement, _db, _payment, _settle


def _admin(db, suffix="a3"):
    row = User(name=f"Master {suffix}", email=f"master-{suffix}@test", cpf=f"master-{suffix}", password_hash="x", role="ADMIN", is_active=True, is_master=True)
    db.add(row)
    db.flush()
    return row


def _agreement_payment(db, suffix, *, paid="0.00", status="OPEN", penalty="0.00", amount="100.00", received=None):
    _member, agreement, rows = _agreement(db, principal=amount, penalty=penalty, paid=paid, status=status)
    payment = _payment(db, None, rows[0], amount=amount if received is None else received, suffix=suffix)
    if received is not None:
        payment.amount_received = Decimal(received)
    settlement = _settle(db, payment)
    admin = _admin(db, suffix)
    db.commit()
    return admin, agreement, rows[0], payment, settlement


@pytest.mark.parametrize(
    ("paid", "status", "received", "expected_before", "expected_after"),
    [("0.00", "OPEN", "25.00", "OPEN", "PARTIAL"), ("25.00", "PARTIAL", "25.00", "PARTIAL", "PARTIAL"), ("0.00", "OPEN", "100.00", "OPEN", "PAID"), ("75.00", "PARTIAL", "25.00", "PARTIAL", "PAID")],
)
def test_agreement_reversal_restores_literal_installment_and_agreement_state(paid, status, received, expected_before, expected_after):
    db = _db()
    admin, agreement, installment, payment, settlement = _agreement_payment(db, f"a3-{paid}-{received}", paid=paid, status=status, received=received)
    settlement_hash = settlement.receipt_hash
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno Agreement", now=datetime(2026, 9, 16, 12, tzinfo=timezone.utc))
    db.commit()

    assert reversal.original_date_kind == "AGREEMENT_INSTALLMENT_DUE_DATE"
    assert reversal.original_due_date == installment.due_date
    assert reversal.original_competence is None
    assert reversal.reversal_competence == date(2026, 9, 1)
    assert installment.status == ("OPEN" if expected_before == "OPEN" else "PARTIAL")
    assert installment.paid_amount == Decimal(paid)
    assert installment.paid_penalty_amount == Decimal("0.00")
    assert installment.paid_at is None
    assert agreement.status == "APPROVED"
    assert agreement.state_revision == 2
    assert payment.status == "approved"
    assert settlement.receipt_hash == settlement_hash
    assert db.query(MemberFinancialEntry).count() == 0
    component = db.query(PaymentReversalComponent).one()
    original = db.get(LedgerEntry, component.original_ledger_entry_id)
    compensating = db.get(LedgerEntry, component.compensating_ledger_entry_id)
    assert original.reference_type == "AGREEMENT_INSTALLMENT_PAYMENT"
    assert compensating.account == original.account == "CAIXINHA"
    assert compensating.amount == original.amount == settlement.amount_applied
    assert compensating.direction == "DEBIT"
    assert compensating.reference_type == "REVERSAL"
    assert compensating.reference_id == str(original.id)
    assert compensating.reversal_of_id == original.id
    assert verify_ledger_chain(db)["status"] == "PASS"


def test_valid_agreement_reversal_is_not_reported_as_cumulative_corruption():
    db = _db()
    admin, _agreement_row, _installment, payment, _settlement = _agreement_payment(
        db, "a3-reconciliation", received="25.00"
    )
    reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Reverter Agreement")
    db.commit()

    findings = {
        item["code"]: item
        for item in build_advanced_reconciliation(db, date.today())["findings"]
    }
    assert findings["AGREEMENT_SETTLEMENT_CUMULATIVE_MISMATCH"]["status"] == "PASS"


@pytest.mark.parametrize("mutation", ("component_missing", "component_wrong", "compensating_wrong", "compensating_amount", "compensating_origin", "original_missing", "receipt"))
def test_invalid_agreement_reversal_does_not_mask_cumulative_mismatch(mutation):
    db = _db()
    admin, _agreement_row, _installment, payment, _settlement = _agreement_payment(
        db, f"a3-reconciliation-{mutation}", received="25.00"
    )
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Reverter Agreement")
    db.commit()
    component = db.query(PaymentReversalComponent).filter_by(payment_reversal_id=reversal.id).one()
    original = db.get(LedgerEntry, component.original_ledger_entry_id)
    compensating = db.get(LedgerEntry, component.compensating_ledger_entry_id)
    if mutation == "component_missing":
        db.delete(component)
    elif mutation in {"component_wrong", "compensating_wrong"}:
        unrelated = post_entry(db, "CAIXINHA", "CREDIT", Decimal("1.00"), "MANUAL_ADJUSTMENT", f"{mutation}-{payment.id}")
        db.flush()
        column = "original_ledger_entry_id" if mutation == "component_wrong" else "compensating_ledger_entry_id"
        db.execute(text(f"UPDATE payment_reversal_components SET {column} = :id WHERE id = :component_id"), {"id": unrelated.id, "component_id": component.id})
    elif mutation == "compensating_amount":
        db.execute(text("UPDATE ledger_entries SET amount = '24.00' WHERE id = :id"), {"id": compensating.id})
    elif mutation == "compensating_origin":
        db.execute(text("UPDATE ledger_entries SET reversal_of_id = NULL WHERE id = :id"), {"id": compensating.id})
    elif mutation == "original_missing":
        db.execute(text("UPDATE ledger_entries SET reference_type = 'OTHER' WHERE id = :id"), {"id": original.id})
    else:
        db.execute(text("UPDATE payment_reversals SET receipt_snapshot_json = :snapshot WHERE id = :id"), {"snapshot": "{}", "id": reversal.id})
    db.commit()
    db.expire_all()

    findings = {
        item["code"]: item
        for item in build_advanced_reconciliation(db, date.today())["findings"]
    }
    assert findings["AGREEMENT_SETTLEMENT_CUMULATIVE_MISMATCH"]["status"] == "FAIL"
    assert findings["AGREEMENT_PAYMENT_REVERSAL_INVALID"]["status"] == "FAIL"


def test_agreement_reversal_blocks_payment_a_after_payment_b_and_allows_b_only():
    db = _db()
    admin, agreement, installment, payment_a, settlement_a = _agreement_payment(db, "a3-a", received="25.00")
    payment_b = _payment(db, None, installment, amount="25.00", suffix="a3-b")
    settlement_b = _settle(db, payment_b)
    with pytest.raises(ValueError, match="Agreement|parcela"):
        reverse_payment(db, payment_id=payment_a.id, admin_id=admin.id, reason="Reverter A")
    db.rollback()
    reversal_b = reverse_payment(db, payment_id=payment_b.id, admin_id=admin.id, reason="Reverter B")
    db.commit()
    assert reversal_b.id
    assert agreement.state_revision == 3
    assert installment.paid_amount == Decimal("25.00")
    with pytest.raises(ValueError, match="Agreement"):
        reverse_payment(db, payment_id=payment_a.id, admin_id=admin.id, reason="Reverter A novamente")
    assert settlement_a.collection_agreement_state_revision_after == 1
    assert settlement_b.collection_agreement_state_revision_after == 2


def test_agreement_reversal_requires_v5_and_exact_current_after():
    db = _db()
    admin, agreement, installment, payment, settlement = _agreement_payment(db, "a3-evidence", received="25.00")
    payment_id = payment.id
    settlement.receipt_version = "v1"
    with db.no_autoflush, pytest.raises(ValueError, match="v5"):
        reverse_payment(db, payment_id=payment_id, admin_id=admin.id, reason="Versão antiga")
    db.rollback()
    settlement.receipt_version = "v5"
    installment.paid_amount = Decimal("24.00")
    db.commit()
    with pytest.raises(ValueError, match="parcela"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estado divergente")
    db.rollback()
    installment.paid_amount = Decimal("25.00")
    agreement.state_revision += 1
    db.commit()
    with pytest.raises(ValueError, match="Agreement"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Revision divergente")


def test_agreement_reversal_rejects_ledger_missing_duplicate_or_wrong_amount():
    for mutation in ("missing", "duplicate", "wrong"):
        db = _db()
        admin, _agreement_row, installment, payment, settlement = _agreement_payment(db, f"a3-ledger-{mutation}", received="25.00")
        original = db.query(LedgerEntry).filter_by(reference_type="AGREEMENT_INSTALLMENT_PAYMENT", reference_id=str(payment.id)).one()
        if mutation == "missing":
            db.execute(text("UPDATE ledger_entries SET reference_type = 'OTHER' WHERE id = :id"), {"id": original.id})
        elif mutation == "duplicate":
            db.add(LedgerEntry(account=original.account, direction=original.direction, amount=original.amount, reference_type=original.reference_type, reference_id=original.reference_id))
        else:
            db.execute(text("UPDATE ledger_entries SET amount = '24.00' WHERE id = :id"), {"id": original.id})
        db.commit()
        with pytest.raises(ValueError, match="Ledger|[Ss]napshot"):
            reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Ledger inválido")
        db.rollback()
        assert db.query(PaymentReversal).count() == 0
        assert installment.paid_amount == Decimal("25.00")
        db.close()


def test_agreement_reversal_is_idempotent_and_creates_one_audit_and_component():
    db = _db()
    admin, _agreement_row, _installment, payment, _settlement = _agreement_payment(db, "a3-idempotent", received="25.00")
    first = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Primeiro estorno")
    db.commit()
    counts = (db.query(PaymentReversal).count(), db.query(PaymentReversalComponent).count(), db.query(AuditLog).count(), db.query(LedgerEntry).count())
    second = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Outro motivo")
    db.commit()
    assert second.id == first.id
    import json
    snapshot = json.loads(first.receipt_snapshot_json)
    assert snapshot["obligation"] == {
        "type": "AGREEMENT_INSTALLMENT",
        "member_id": _agreement_row.member_id,
        "agreement_installment_id": _installment.id,
        "collection_agreement_id": _agreement_row.id,
    }
    assert (db.query(PaymentReversal).count(), db.query(PaymentReversalComponent).count(), db.query(AuditLog).count(), db.query(LedgerEntry).count()) == counts


def test_agreement_reversal_authorization_reason_and_closing():
    db = _db()
    admin, agreement, installment, payment, _settlement = _agreement_payment(db, "a3-auth", received="25.00")
    non_master = User(name="Admin", email="admin-a3@test", cpf="admin-a3", password_hash="x", role="ADMIN", is_active=True, is_master=False)
    db.add(non_master)
    db.flush()
    with pytest.raises(ValueError, match="Master"):
        reverse_payment(db, payment_id=payment.id, admin_id=non_master.id, reason="Tentativa")
    with pytest.raises(ValueError, match="motivo"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="x")
    closing = MonthlyClosing(competence=date(2026, 1, 1), status="CLOSED", ledger_balance=Decimal("10.00"))
    db.add(closing)
    db.commit()
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno fechado", now=datetime(2026, 9, 20, tzinfo=timezone.utc))
    db.commit()
    assert db.get(MonthlyClosing, closing.id).status == "CLOSED"
    assert reversal.reversal_competence == date(2026, 9, 1)
    assert agreement.status == "APPROVED"
    assert installment.paid_amount == Decimal("0.00")


def test_agreement_reversal_late_failure_rolls_back_all_mutations(monkeypatch):
    db = _db()
    admin, agreement, installment, payment, settlement = _agreement_payment(db, "a3-rollback", received="25.00")
    before = (agreement.status, agreement.state_revision, installment.status, installment.paid_amount, installment.paid_penalty_amount, settlement.receipt_hash)

    def fail_after_mutation(_agreement):
        raise ValueError("falha tardia A3")

    monkeypatch.setattr(reversal_service, "touch_collection_agreement", fail_after_mutation)
    with pytest.raises(ValueError, match="falha tardia"):
        reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Falha tardia")
    db.rollback()
    assert (agreement.status, agreement.state_revision, installment.status, installment.paid_amount, installment.paid_penalty_amount, settlement.receipt_hash) == before
    assert db.query(PaymentReversal).count() == 0
    assert db.query(PaymentReversalComponent).count() == 0
    assert db.query(LedgerEntry).filter_by(reference_type="REVERSAL").count() == 0


def test_generic_reverse_entry_blocks_agreement_payment_but_not_manual_entry():
    db = _db()
    _admin_row, _agreement_row, _installment, payment, _settlement = _agreement_payment(db, "a3-guard", received="25.00")
    original = db.query(LedgerEntry).filter_by(reference_id=str(payment.id)).one()
    with pytest.raises(ValueError, match="PaymentReversal"):
        reverse_entry(db, original, "Estorno isolado")
    manual = post_entry(db, "CAIXINHA", "CREDIT", Decimal("3.00"), "MANUAL_ADJUSTMENT", "a3-manual")
    db.flush()
    assert reverse_entry(db, manual, "Ajuste manual válido").reversal_of_id == manual.id
