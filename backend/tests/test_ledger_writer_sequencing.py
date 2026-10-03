"""Writer sequencing with the production SessionLocal autoflush contract."""
import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import Base
from app.models import LedgerEntry, MemberFinancialEntry
from app.services import ledger
from app.services.loan_payments_v17 import apply_confirmed_payment
from app.services.payment_settlement import settle_confirmed_pix_payment
from test_h4_c8_b3_a376g_versioned_settlement_reversal_payoff import (
    _single_installment, _payment as versioned_payment,
)
from test_payment_settlement_v103 import _member, _installment, _payment


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as session:
        yield session
    engine.dispose()


class LedgerClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 1, 2, 59, 59, tzinfo=timezone.utc)


def post(db, version, reference):
    args = (db, "CAIXINHA", "CREDIT", Decimal("12.30"), "SEQUENCING_TEST", reference)
    if version == 1:
        return ledger.post_entry(*args)
    return ledger.post_entry_v2(*args, financial_date=date(2026, 9, 30))


def expected_hash(reference, previous_hash, version):
    # Independent copy of the public payload contract: no database ID.
    payload = {
        "account": "CAIXINHA", "direction": "CREDIT", "amount": "12.30",
        "reference_type": "SEQUENCING_TEST", "reference_id": reference,
        "reversal_of_id": None, "created_at": "2026-10-01T02:59:59+00:00",
        "previous_hash": previous_hash,
    }
    if version == 2:
        payload.update(hash_version=2, financial_date="2026-09-30")
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@pytest.mark.parametrize("versions", [(1, 1), (2, 2), (1, 2), (2, 1)])
def test_consecutive_posts_without_caller_flush(db, monkeypatch, versions):
    monkeypatch.setattr(ledger, "datetime", LedgerClock)
    assert db.autoflush is False
    first = post(db, versions[0], "first")
    second = post(db, versions[1], "second")
    # Reproduction: both calls happen before any caller flush.
    db.flush()
    assert second.previous_hash == first.entry_hash
    assert first.entry_hash == expected_hash("first", None, versions[0])
    assert second.entry_hash == expected_hash("second", first.entry_hash, versions[1])
    for row, version in zip((first, second), versions):
        assert row.id is not None and inspect(row).persistent
        assert row.hash_version == (2 if version == 2 else None)
        assert row.financial_date == (date(2026, 9, 30) if version == 2 else None)
    assert db.in_transaction()
    assert ledger.verify_ledger_chain(db) == {"status": "PASS", "entries": 2, "errors": []}


@pytest.mark.parametrize("version", [1, 2])
def test_internal_flush_is_rollbackable(db, version):
    first = post(db, version, "rollback-first")
    second = post(db, version, "rollback-second")
    assert first.id is not None and second.id is not None
    assert second.previous_hash == first.entry_hash
    assert db.query(LedgerEntry).count() == 2
    db.rollback()
    with Session(db.get_bind(), autoflush=False) as observer:
        assert observer.query(LedgerEntry).count() == 0


def test_writer_returned_entry_remains_immutable(db):
    row = post(db, 1, "immutable")
    row.amount = Decimal("99.00")
    with pytest.raises(RuntimeError, match="imutável"):
        db.flush()
    db.rollback()


def assert_loan_components(db, interest, penalty, principal):
    rows = db.query(LedgerEntry).order_by(LedgerEntry.id).all()
    assert len(rows) == 2
    assert rows[1].previous_hash == rows[0].entry_hash
    assert {row.reference_type: row.amount for row in rows} == {
        "LOAN_INTEREST_PAYMENT": interest, "LOAN_PENALTY_PAYMENT": penalty,
    }
    assert ledger.verify_ledger_chain(db)["status"] == "PASS"
    entries = db.query(MemberFinancialEntry).filter_by(
        entry_type="LOAN_PRINCIPAL_PAYMENT"
    ).all()
    assert len(entries) == 1 and entries[0].amount == principal


def test_versioned_settlement_interest_and_penalty_without_autoflush(db, monkeypatch):
    monkeypatch.setattr(settings, "loan_late_charge_effective_date", date(2026, 1, 1))
    _, _, installment = _single_installment(db, "sequencing-versioned")
    payment = versioned_payment(db, installment, "sequencing-versioned", "131.33")
    settlement = settle_confirmed_pix_payment(
        db, payment, confirmation_source="TEST_PROVIDER",
        confirmed_at=datetime(2026, 1, 3, 15, tzinfo=timezone.utc),
    )
    assert (settlement.principal_applied, settlement.interest_applied,
            settlement.penalty_applied) == (
        Decimal("100.00"), Decimal("20.00"), Decimal("11.33"),
    )
    assert (settlement.fixed_penalty_applied, settlement.late_interest_applied) == (
        Decimal("10.00"), Decimal("1.33"),
    )
    assert installment.paid_amount == Decimal("120.00")
    assert installment.paid_penalty_amount == Decimal("11.33")
    assert_loan_components(db, Decimal("20.00"), Decimal("11.33"), Decimal("100.00"))


@pytest.mark.parametrize("via_settlement", [False, True])
def test_legacy_loan_interest_and_penalty_without_autoflush(db, monkeypatch, via_settlement):
    monkeypatch.setattr(settings, "loan_late_charge_effective_date", date(2027, 1, 1))
    member = _member(db, "sequencing-legacy")
    _, installment = _installment(
        db, member, penalty="10.00", due_date=date(2026, 1, 10),
    )
    payment = _payment(
        db, suffix="sequencing-legacy", amount="50.00",
        reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id),
    )
    if via_settlement:
        settlement = settle_confirmed_pix_payment(
            db, payment, confirmation_source="TEST_PROVIDER",
            confirmed_at=datetime(2026, 1, 11, 12, tzinfo=timezone.utc),
        )
        assert (settlement.principal_applied, settlement.interest_applied,
                settlement.penalty_applied) == (
            Decimal("20.00"), Decimal("20.00"), Decimal("10.00"),
        )
    else:
        assert apply_confirmed_payment(db, payment, installment)
    db.flush()
    assert installment.paid_amount == Decimal("40.00")
    assert installment.paid_penalty_amount == Decimal("10.00")
    assert installment.status == "PARTIAL"
    assert_loan_components(db, Decimal("20.00"), Decimal("10.00"), Decimal("20.00"))
