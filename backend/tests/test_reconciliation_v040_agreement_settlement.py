from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import AgreementInstallment, CollectionAgreement, Group, LedgerEntry, Loan, Member, Payment, User
from app.services.payment_settlement import settle_confirmed_pix_payment
from app.services.reconciliation_v040 import build_advanced_reconciliation


def newdb():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def agreement(db, *, principal="100.00", penalty="10.00", status="OPEN"):
    group = Group(name="Agreement reconciliation")
    user = User(name="Agreement member", email="agreement-reconciliation@example.test", cpf="agreement-reconciliation", password_hash="x")
    db.add_all([group, user])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id)
    db.add(member)
    db.flush()
    loan = Loan(member_id=member.id, principal=Decimal("100.00"), monthly_rate=Decimal("0.10"), installments=1, status="ACTIVE")
    db.add(loan)
    db.flush()
    collection = CollectionAgreement(loan_id=loan.id, member_id=member.id, requested_by=user.id, status="APPROVED", installments=1, total_amount=Decimal(principal) + Decimal(penalty), snapshot="{}")
    db.add(collection)
    db.flush()
    installment = AgreementInstallment(agreement_id=collection.id, number=1, due_date=date.today() + timedelta(days=10), principal=Decimal(principal), penalty_amount=Decimal(penalty), amount=Decimal(principal) + Decimal(penalty), status=status)
    db.add(installment)
    db.flush()
    return member, collection, installment


def payment(db, installment, amount="30.00"):
    row = Payment(provider="test", provider_payment_id=f"provider-{installment.id}-{amount}-{db.query(Payment).count()}", idempotency_key=f"idempotency-{installment.id}-{amount}-{db.query(Payment).count()}", amount=Decimal(amount), amount_received=Decimal(amount), status="approved", reference_type="AGREEMENT_INSTALLMENT", reference_id=str(installment.id))
    db.add(row)
    db.flush()
    return row


def finding(db, code):
    return next(item for item in build_advanced_reconciliation(db, date.today())["findings"] if item["code"] == code)


def settle(db, installment, amount="30.00"):
    row = payment(db, installment, amount)
    settlement = settle_confirmed_pix_payment(db, row, confirmation_source="TEST", confirmed_at=datetime.now(timezone.utc))
    db.commit()
    return row, settlement


def ignore_sqlite_checks(db):
    db.connection().exec_driver_sql("PRAGMA ignore_check_constraints=ON")


def test_exclusively_legacy_valid_payment_without_settlement_is_accepted():
    db = newdb()
    _, _, installment = agreement(db)
    legacy = payment(db, installment, "30.00")
    db.add(LedgerEntry(account="CAIXINHA", direction="CREDIT", amount=Decimal("30.00"), reference_type="AGREEMENT_INSTALLMENT_PAYMENT", reference_id=str(legacy.id)))
    db.commit()
    assert finding(db, "AGREEMENT_SETTLEMENT_INVALID")["status"] == "PASS"
    assert finding(db, "AGREEMENT_SETTLEMENT_LEDGER_MISMATCH")["status"] == "PASS"
    assert finding(db, "AGREEMENT_SETTLEMENT_CUMULATIVE_MISMATCH")["status"] == "PASS"
    assert finding(db, "APPROVED_PAYMENTS")["status"] == "PASS"


def test_legacy_approved_payment_suppresses_only_cumulative_mismatch():
    db = newdb()
    _, _, installment = agreement(db, penalty="0.00")
    settle(db, installment, "40.00")
    installment.paid_amount = Decimal("30.00")
    legacy = payment(db, installment, "10.00")
    db.add(LedgerEntry(account="CAIXINHA", direction="CREDIT", amount=Decimal("10.00"), reference_type="AGREEMENT_INSTALLMENT_PAYMENT", reference_id=str(legacy.id)))
    db.commit()
    assert finding(db, "APPROVED_PAYMENTS")["status"] == "PASS"
    assert finding(db, "AGREEMENT_SETTLEMENT_CUMULATIVE_MISMATCH")["status"] == "PASS"


def test_nonapproved_other_type_and_other_installment_do_not_suppress_cumulative_mismatch():
    for kind in ("nonapproved", "other_type", "other_installment"):
        db = newdb()
        _, collection, installment = agreement(db, penalty="0.00")
        settle(db, installment, "40.00")
        installment.paid_amount = Decimal("30.00")
        if kind == "nonapproved":
            extra = payment(db, installment, "10.00")
            extra.status = "PENDING"
        elif kind == "other_type":
            extra = payment(db, installment, "10.00")
            extra.reference_type = "CONTRIBUTION"
        else:
            other = AgreementInstallment(agreement_id=collection.id, number=2, due_date=date.today() + timedelta(days=20), principal=Decimal("10.00"), penalty_amount=Decimal("0.00"), amount=Decimal("10.00"), status="OPEN")
            db.add(other)
            db.flush()
            payment(db, other, "10.00")
        db.commit()
        assert finding(db, "AGREEMENT_SETTLEMENT_CUMULATIVE_MISMATCH")["status"] == "FAIL"


def test_reference_and_member_mismatch_are_structured_invalid_findings():
    db = newdb()
    member, _, installment = agreement(db)
    payment_row, settlement = settle(db, installment, "30.00")
    payment_row.reference_id = "999999"
    settlement.member_id = member.id + 999
    db.commit()
    assert finding(db, "AGREEMENT_SETTLEMENT_INVALID")["status"] == "FAIL"


def test_incompatible_settlement_cannot_escape_agreement_reconciliation():
    db = newdb()
    member, _, installment = agreement(db)
    payment_row, settlement = settle(db, installment, "30.00")
    ignore_sqlite_checks(db)
    settlement.obligation_type = "CONTRIBUTION"
    settlement.agreement_installment_id = None
    settlement.member_id = member.id
    db.flush()
    assert finding(db, "AGREEMENT_SETTLEMENT_INVALID")["status"] == "FAIL"


def test_equations_interest_and_negative_values_are_invalid_when_constraints_are_bypassed():
    db = newdb()
    _, _, installment = agreement(db)
    _, settlement = settle(db, installment, "30.00")
    ignore_sqlite_checks(db)
    settlement.interest_applied = Decimal("1.00")
    settlement.amount_applied = Decimal("31.00")
    settlement.receipt_snapshot_json = "{}"
    settlement.receipt_hash = "bad"
    db.flush()
    assert finding(db, "AGREEMENT_SETTLEMENT_INVALID")["status"] == "FAIL"


def test_ledger_missing_duplicate_and_wrong_amount_are_detected():
    for mutation in ("missing", "duplicate", "wrong"):
        db = newdb()
        _, _, installment = agreement(db)
        payment_row, _ = settle(db, installment, "30.00")
        query = db.query(LedgerEntry).filter_by(reference_type="AGREEMENT_INSTALLMENT_PAYMENT", reference_id=str(payment_row.id))
        if mutation == "missing":
            query.delete()
        elif mutation == "duplicate":
            row = query.one()
            db.add(LedgerEntry(account=row.account, direction=row.direction, amount=row.amount, reference_type=row.reference_type, reference_id=row.reference_id))
        else:
            db.execute(text("UPDATE ledger_entries SET amount = :amount WHERE reference_type = :reference_type AND reference_id = :reference_id"), {"amount": "29.00", "reference_type": "AGREEMENT_INSTALLMENT_PAYMENT", "reference_id": str(payment_row.id)})
        db.commit()
        assert finding(db, "AGREEMENT_SETTLEMENT_LEDGER_MISMATCH")["status"] == "FAIL"


def test_ledger_direction_account_and_extra_entry_are_detected():
    for mutation in ("direction", "account", "extra"):
        db = newdb()
        _, _, installment = agreement(db)
        payment_row, _ = settle(db, installment, "30.00")
        if mutation == "extra":
            db.add(LedgerEntry(account="CAIXINHA", direction="DEBIT", amount=Decimal("1.00"), reference_type="OTHER", reference_id=str(payment_row.id)))
        else:
            column = "direction" if mutation == "direction" else "account"
            value = "DEBIT" if mutation == "direction" else "OUTRA_CONTA"
            db.execute(text(f"UPDATE ledger_entries SET {column} = :value WHERE reference_type = :reference_type AND reference_id = :reference_id"), {"value": value, "reference_type": "AGREEMENT_INSTALLMENT_PAYMENT", "reference_id": str(payment_row.id)})
        db.commit()
        assert finding(db, "AGREEMENT_SETTLEMENT_LEDGER_MISMATCH")["status"] == "FAIL"


def test_receipt_number_snapshot_and_hash_are_validated():
    db = newdb()
    _, _, installment = agreement(db)
    _, settlement = settle(db, installment, "30.00")
    settlement.receipt_hash = hashlib.sha256(b"wrong").hexdigest()
    db.commit()
    assert finding(db, "AGREEMENT_SETTLEMENT_INVALID")["status"] == "FAIL"


def test_receipt_number_version_and_snapshot_are_validated():
    for field, value in (("receipt_number", "wrong"), ("receipt_version", "v2"), ("receipt_snapshot_json", "{}")):
        db = newdb()
        _, _, installment = agreement(db)
        _, settlement = settle(db, installment, "30.00")
        if field == "receipt_version":
            ignore_sqlite_checks(db)
        setattr(settlement, field, value)
        db.flush()
        assert finding(db, "AGREEMENT_SETTLEMENT_INVALID")["status"] == "FAIL"


def test_multiple_partial_settlements_have_correct_cumulative_reconciliation():
    db = newdb()
    _, _, installment = agreement(db, penalty="0.00")
    settle(db, installment, "40.00")
    settle(db, installment, "60.00")
    assert finding(db, "AGREEMENT_SETTLEMENT_CUMULATIVE_MISMATCH")["status"] == "PASS"


def test_cumulative_mismatch_is_detected_without_legacy_payment_and_suppressed_with_legacy():
    db = newdb()
    _, _, installment = agreement(db, penalty="0.00")
    settle(db, installment, "40.00")
    installment.paid_amount = Decimal("30.00")
    db.commit()
    assert finding(db, "AGREEMENT_SETTLEMENT_CUMULATIVE_MISMATCH")["status"] == "FAIL"

    legacy = payment(db, installment, "10.00")
    db.add(LedgerEntry(account="CAIXINHA", direction="CREDIT", amount=Decimal("10.00"), reference_type="AGREEMENT_INSTALLMENT_PAYMENT", reference_id=str(legacy.id)))
    db.commit()
    assert finding(db, "AGREEMENT_SETTLEMENT_CUMULATIVE_MISMATCH")["status"] == "PASS"


def test_cumulative_overapplication_of_principal_and_penalty_is_detected():
    for principal, penalty, field in (("100.00", "0.00", "principal_applied"), ("0.00", "100.00", "penalty_applied")):
        db = newdb()
        _, _, installment = agreement(db, principal=principal, penalty=penalty)
        _, settlement = settle(db, installment, "100.00")
        ignore_sqlite_checks(db)
        if field == "principal_applied":
            settlement.principal_applied = Decimal("110.00")
            installment.paid_amount = Decimal("110.00")
        else:
            settlement.penalty_applied = Decimal("110.00")
            installment.paid_penalty_amount = Decimal("110.00")
        settlement.amount_applied = Decimal("110.00")
        settlement.amount_received = Decimal("110.00")
        db.flush()
        assert finding(db, "AGREEMENT_SETTLEMENT_CUMULATIVE_MISMATCH")["status"] == "FAIL"


def test_historical_status_is_not_compared_to_current_status_after_later_payment():
    db = newdb()
    _, _, installment = agreement(db, principal="100.00", penalty="0.00")
    first_payment, first = settle(db, installment, "40.00")
    assert first.obligation_status_after == "PARTIAL"
    settle(db, installment, "60.00")
    assert finding(db, "AGREEMENT_SETTLEMENT_INVALID")["status"] == "PASS"
    assert first_payment.id is not None


def test_missing_or_tied_confirmed_at_never_causes_ordering_exception():
    db = newdb()
    _, _, installment = agreement(db, principal="100.00", penalty="0.00")
    _, first = settle(db, installment, "40.00")
    _, second = settle(db, installment, "60.00")
    db.autoflush = False
    first.confirmed_at = None
    second.confirmed_at = None
    missing_result = finding(db, "AGREEMENT_SETTLEMENT_INVALID")
    assert "sequência histórica" not in missing_result["details"]

    first.confirmed_at = second.confirmed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    tied_result = finding(db, "AGREEMENT_SETTLEMENT_INVALID")
    assert "sequência histórica" not in tied_result["details"]


def test_collection_agreement_status_is_checked_in_both_directions():
    db = newdb()
    _, collection, installment = agreement(db, principal="100.00", penalty="0.00")
    collection.status = "SETTLED"
    db.commit()
    assert finding(db, "AGREEMENT_COLLECTION_STATUS_MISMATCH")["status"] == "FAIL"

    collection.status = "ACTIVE"
    installment.status = "PAID"
    db.commit()
    assert finding(db, "AGREEMENT_COLLECTION_STATUS_MISMATCH")["status"] == "FAIL"
