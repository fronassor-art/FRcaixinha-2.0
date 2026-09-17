"""Integration coverage for read-only reversal netting in reconciliation."""

import hashlib
import json
from datetime import date

from sqlalchemy import event
import pytest
from app.models import LedgerEntry, PaymentReversalComponent
from app.services.reconciliation_v040 import build_advanced_reconciliation
from app.services.payment_reversal_evidence import validate_reversal_effect
from test_payment_reversal_contribution_v104 import _setup as contribution_setup
from test_payment_reversal_loan_v105 import _loan_payment
from test_payment_reversal_agreement_v106 import _agreement_payment
from app.services.payment_reversal import reverse_payment


def _findings(db):
    return {item["code"]: item for item in build_advanced_reconciliation(db, date(2026, 9, 1))["findings"]}


def _db_for_contribution():
    from test_payment_reversal_contribution_v104 import _db
    return _db()


def test_contribution_reversal_neutralizes_only_with_complete_chain():
    db = _db_for_contribution()
    admin, _contribution, payment, _settlement = contribution_setup(db, received="40.00", suffix="h1-contribution")
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="H1 contribution")
    db.commit()
    assert _findings(db)["CONTRIBUTIONS"]["status"] == "PASS"

    component = db.query(PaymentReversalComponent).filter_by(payment_reversal_id=reversal.id).one()
    db.delete(component)
    db.commit()
    findings = _findings(db)
    assert findings["CONTRIBUTIONS"]["status"] == "FAIL"
    assert findings["CONTRIBUTION_PAYMENT_REVERSAL_INVALID"]["status"] == "FAIL"


def test_loan_reversal_neutralizes_ledger_components_without_double_counting_mfe():
    db = __import__("test_payment_reversal_loan_v105", fromlist=["_db"])._db()
    admin, _member, _loan, _installment, payment, _settlement = _loan_payment(db, "h1-loan", amount="120.00", interest="20.00")
    reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="H1 loan")
    db.commit()
    findings = _findings(db)
    assert findings["PIX_INSTALLMENT_SETTLEMENT"]["status"] == "PASS"


def test_agreement_reversal_neutralizes_global_aggregate_and_active_payment_counts():
    db = __import__("test_payment_reversal_agreement_v106", fromlist=["_db"])._db()
    admin, _agreement, _installment, payment, _settlement = _agreement_payment(db, "h1-agreement", received="25.00")
    reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="H1 agreement")
    db.commit()
    findings = _findings(db)
    assert findings["AGREEMENT_SETTLEMENT_CUMULATIVE_MISMATCH"]["status"] == "PASS"
    assert findings["AGREEMENT_PAYMENT_REVERSAL_INVALID"]["status"] == "PASS"


def test_unrelated_reversal_and_generic_reversal_do_not_neutralize_payment():
    db = _db_for_contribution()
    _admin, _contribution, payment, _settlement = contribution_setup(db, received="40.00", suffix="h1-active")
    db.commit()
    before = [(row.id, row.amount, row.direction, row.reference_type, row.reference_id) for row in db.query(LedgerEntry).all()]
    findings = _findings(db)
    assert findings["CONTRIBUTION_PAYMENT_REVERSAL_INVALID"]["status"] == "PASS"
    after = [(row.id, row.amount, row.direction, row.reference_type, row.reference_id) for row in db.query(LedgerEntry).all()]
    assert before == after
    assert payment.status == "approved"


@pytest.mark.parametrize("mutation", ("payment", "settlement", "obligation", "amount"))
def test_a3_crossed_or_recalculated_snapshot_is_rejected(mutation):
    db = __import__("test_payment_reversal_agreement_v106", fromlist=["_db"])._db()
    admin, _agreement, _installment, payment, _settlement = _agreement_payment(db, "h1-snapshot", received="25.00")
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="H1 snapshot")
    db.flush()
    snapshot = json.loads(reversal.receipt_snapshot_json)
    if mutation == "payment":
        snapshot["payment"]["id"] = payment.id + 999
    elif mutation == "settlement":
        snapshot["settlement"]["id"] = _settlement.id + 999
    elif mutation == "obligation":
        snapshot["obligation"]["agreement_installment_id"] = _installment.id + 999
    else:
        snapshot["amounts"]["amount_applied"] = "99.00"
    reversal.receipt_snapshot_json = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    reversal.receipt_hash = hashlib.sha256(reversal.receipt_snapshot_json.encode()).hexdigest()
    db.commit()
    assert validate_reversal_effect(db, reversal)[0] is False
    findings = _findings(db)
    assert findings["AGREEMENT_PAYMENT_REVERSAL_INVALID"]["status"] == "FAIL"
    assert findings["AGREEMENT_SETTLEMENT_CUMULATIVE_MISMATCH"]["status"] == "FAIL"


def test_payment_linked_extra_ledger_invalidates_agreement_reversal():
    db = __import__("test_payment_reversal_agreement_v106", fromlist=["_db"])._db()
    admin, _agreement, _installment, payment, _settlement = _agreement_payment(db, "h1-extra-ledger", received="25.00")
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="H1 extra ledger")
    db.add(LedgerEntry(account="CAIXINHA", direction="CREDIT", amount="1.00", reference_type="MANUAL_ADJUSTMENT", reference_id=str(payment.id)))
    db.commit()
    assert validate_reversal_effect(db, reversal)[0] is False
    findings = _findings(db)
    assert findings["AGREEMENT_PAYMENT_REVERSAL_INVALID"]["status"] == "FAIL"


def test_duplicate_compensating_ledger_invalidates_reversal():
    db = __import__("test_payment_reversal_agreement_v106", fromlist=["_db"])._db()
    admin, _agreement, _installment, payment, _settlement = _agreement_payment(db, "h1-duplicate-comp", received="25.00")
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="H1 duplicate comp")
    component = db.query(PaymentReversalComponent).filter_by(payment_reversal_id=reversal.id).one()
    original = db.get(LedgerEntry, component.original_ledger_entry_id)
    db.add(LedgerEntry(account="CAIXINHA", direction="DEBIT", amount=original.amount, reference_type="REVERSAL", reference_id=str(original.id), reversal_of_id=original.id))
    db.commit()
    assert validate_reversal_effect(db, reversal)[0] is False


def test_loan_zero_ledger_components_are_valid_when_absent():
    db = __import__("test_payment_reversal_loan_v105", fromlist=["_db"])._db()
    admin, _member, _loan, _installment, payment, _settlement = _loan_payment(db, "h1-zero-components", amount="100.00", interest="0.00", penalty="0.00")
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="H1 zero components")
    db.commit()
    assert validate_reversal_effect(db, reversal) == (True, "")


def test_validation_does_not_autoflush_dirty_session():
    db = _db_for_contribution()
    admin, _contribution, payment, _settlement = contribution_setup(db, received="40.00", suffix="h1-no-autoflush")
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="H1 no autoflush")
    db.commit()
    flushes = []

    @event.listens_for(db.bind, "before_cursor_execute")
    def detect_update(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")):
            flushes.append(statement)

    payment.status = "tampered-in-memory"
    assert validate_reversal_effect(db, reversal)[0] is False
    assert flushes == []
    db.rollback()
    db.refresh(payment)
    assert payment.status == "approved"
    event.remove(db.bind, "before_cursor_execute", detect_update)
