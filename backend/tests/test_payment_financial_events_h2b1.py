from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.models import LedgerEntry, MemberFinancialEntry, Payment
from app.services.payment_financial_events import payment_financial_events
from app.services.payment_reversal import reverse_payment
from app.services.ledger import reverse_entry
import app.services.payment_financial_events as financial_events_service

from test_payment_reversal_contribution_v104 import _db as contribution_db, _setup as setup_contribution
from test_payment_reversal_loan_v105 import _loan_payment
from test_payment_reversal_agreement_v106 import _agreement_payment
from test_payment_settlement_v103 import _installment, _member, _payment, _settle
from test_agreement_payment_settlement_v104 import _agreement, _payment as agreement_payment, _settle as agreement_settle


def test_contribution_original_and_valid_reversal_are_signed_events():
    db = contribution_db()
    admin, _contribution, payment, _settlement = setup_contribution(db, suffix="h2b1-contribution")
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno H2B1", now=datetime(2026, 10, 1, tzinfo=timezone.utc))
    db.commit()

    events = payment_financial_events(db)
    assert [(item.component, item.amount, item.source) for item in events] == [
        ("CONTRIBUTION", Decimal("100.00"), "ORIGINAL"),
        ("CONTRIBUTION", Decimal("-100.00"), "REVERSAL"),
    ]
    assert events[1].payment_reversal_id == reversal.id


def test_payment_and_target_must_be_causally_consistent():
    db = contribution_db()
    _admin, _contribution, payment, settlement = setup_contribution(db, suffix="h2b1-inconsistent")
    payment.reference_id = "999999"
    assert payment_financial_events(db) == ()
    settlement.payment_id = 999999
    assert payment_financial_events(db) == ()
    db.rollback()


@pytest.mark.parametrize("version", ("v1", "v2", "v3", "v4"))
def test_each_loan_settlement_version_emits_original_components(version):
    db = contribution_db()
    _admin, _member, _loan, installment, payment, settlement = _loan_payment(
        db, f"h2b1-loan-{version}", amount="130.00", interest="20.00", penalty="10.00"
    )
    settlement.receipt_version = version
    events = payment_financial_events(db)
    assert sorted((item.component, item.amount) for item in events) == sorted([
        ("LOAN_PRINCIPAL", Decimal("100.00")),
        ("LOAN_INTEREST", Decimal("20.00")),
        ("LOAN_PENALTY", Decimal("10.00")),
    ])
    assert all(item.payment_id == payment.id and item.settlement_id == settlement.id for item in events)
    assert installment.id == settlement.loan_installment_id
    db.rollback()


@pytest.mark.parametrize("version", ("v1", "v5"))
def test_each_agreement_settlement_version_emits_original_without_mfe(version):
    db = contribution_db()
    _admin, _agreement, installment, payment, settlement = _agreement_payment(
        db, f"h2b1-agreement-{version}", received="25.00"
    )
    settlement.receipt_version = version
    events = payment_financial_events(db)
    assert [(item.component, item.amount) for item in events] == [("AGREEMENT", Decimal("25.00"))]
    assert events[0].payment_id == payment.id
    assert settlement.agreement_installment_id == installment.id
    db.rollback()


def test_invalid_or_generic_contribution_reversal_has_no_negative_event():
    db = contribution_db()
    admin, _contribution, payment, _settlement = setup_contribution(db, suffix="h2b1-invalid")
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno H2B1")
    db.commit()
    reversal.receipt_hash = "invalid"
    db.commit()
    assert [item.amount for item in payment_financial_events(db)] == [Decimal("100.00")]


def test_unexpected_validator_error_propagates(monkeypatch):
    db = contribution_db()
    admin, _contribution, payment, _settlement = setup_contribution(db, suffix="h2b1-error")
    reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno H2B1")
    db.commit()

    def explode(*_args, **_kwargs):
        raise RuntimeError("falha inesperada H2B1")

    monkeypatch.setattr(financial_events_service, "validate_reversal_effect", explode)
    with pytest.raises(RuntimeError, match="falha inesperada"):
        payment_financial_events(db)


def test_generic_ledger_reversal_is_not_a_payment_reversal_event():
    db = contribution_db()
    _admin, _contribution, payment, _settlement = setup_contribution(db, suffix="h2b1-generic")
    original = db.query(LedgerEntry).filter_by(reference_id=str(payment.id)).one()
    reverse_entry(db, original, "Ajuste genérico H2B1")
    db.commit()
    assert [item.amount for item in payment_financial_events(db)] == [Decimal("100.00")]


def test_loan_components_are_independent_and_zero_components_are_omitted():
    db = contribution_db()
    admin, _member, _loan, _installment, payment, _settlement = _loan_payment(
        db, "h2b1-loan", amount="130.00", interest="20.00", penalty="10.00"
    )
    reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno H2B1")
    db.commit()
    events = payment_financial_events(db)
    assert sorted((item.component, item.amount) for item in events) == sorted([
        ("LOAN_PRINCIPAL", Decimal("100.00")),
        ("LOAN_INTEREST", Decimal("20.00")),
        ("LOAN_PENALTY", Decimal("10.00")),
        ("LOAN_PRINCIPAL", Decimal("-100.00")),
        ("LOAN_INTEREST", Decimal("-20.00")),
        ("LOAN_PENALTY", Decimal("-10.00")),
    ])


def test_loan_zero_components_and_penalty_only_do_not_create_fictitious_events():
    db = contribution_db()
    admin, _member, _loan, _installment, payment, _settlement = _loan_payment(
        db, "h2b1-loan-zero", amount="10.00", interest="0.00", penalty="10.00"
    )
    reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno H2B1")
    db.commit()
    events = payment_financial_events(db)
    assert [(item.component, item.amount) for item in events] == [
        ("LOAN_PENALTY", Decimal("10.00")),
        ("LOAN_PENALTY", Decimal("-10.00")),
    ]


def test_two_loan_settlements_on_one_installment_remain_independent():
    db = contribution_db()
    member = _member(db, "h2b1-loan-multiple")
    _loan, installment = _installment(db, member, amount="120.00", interest="20.00")
    first_payment = _payment(db, suffix="h2b1-loan-first", amount="50.00", reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id))
    first = _settle(db, first_payment, when=datetime(2027, 1, 10, tzinfo=timezone.utc))
    second_payment = _payment(db, suffix="h2b1-loan-second", amount="20.00", reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id))
    second = _settle(db, second_payment, when=datetime(2027, 1, 11, tzinfo=timezone.utc))
    events = payment_financial_events(db)
    assert {item.settlement_id for item in events} == {first.id, second.id}
    assert sum((item.amount for item in events if item.component == "LOAN_PRINCIPAL"), Decimal("0")) == Decimal("50.00")
    assert len({item.event_id for item in events}) == len(events)


def test_two_agreement_settlements_remain_independent():
    db = contribution_db()
    member, agreement_obj, rows = _agreement(db, principal="100.00")
    first_payment = agreement_payment(db, member, rows[0], amount="25.00", suffix="h2b1-agreement-first")
    first = agreement_settle(db, first_payment)
    second_payment = agreement_payment(db, member, rows[0], amount="25.00", suffix="h2b1-agreement-second")
    second = agreement_settle(db, second_payment)
    events = payment_financial_events(db)
    assert {item.settlement_id for item in events} == {first.id, second.id}
    assert [item.amount for item in events if item.component == "AGREEMENT"] == [Decimal("25.00"), Decimal("25.00")]
    assert len({item.event_id for item in events}) == 2


def test_agreement_v5_has_no_mfe_dependency_and_invalid_reversal_stays_positive():
    db = contribution_db()
    admin, _agreement, _installment, payment, _settlement = _agreement_payment(
        db, "h2b1-agreement", received="25.00"
    )
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno H2B1")
    db.commit()
    reversal.receipt_snapshot_json = "{}"
    db.commit()
    events = payment_financial_events(db)
    assert [(item.component, item.amount) for item in events] == [("AGREEMENT", Decimal("25.00"))]


def test_valid_agreement_reversal_adds_only_the_negative_agreement_event():
    db = contribution_db()
    admin, _agreement, _installment, payment, _settlement = _agreement_payment(
        db, "h2b1-agreement-valid", received="25.00"
    )
    reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno H2B1")
    db.commit()
    events = payment_financial_events(db)
    assert [(item.component, item.amount) for item in events] == [
        ("AGREEMENT", Decimal("25.00")),
        ("AGREEMENT", Decimal("-25.00")),
    ]
    assert db.query(MemberFinancialEntry).count() == 0


def test_old_reversal_is_found_by_reversal_period_not_original_period():
    db = contribution_db()
    admin, _contribution, payment, settlement = setup_contribution(db, suffix="h2b1-period")
    settlement.confirmed_at = datetime(2026, 1, 31, 12, tzinfo=timezone.utc)
    db.commit()
    reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno H2B1", now=datetime(2026, 3, 1, tzinfo=timezone.utc))
    db.commit()

    january = payment_financial_events(db, start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 2, 1, tzinfo=timezone.utc))
    march = payment_financial_events(db, start=datetime(2026, 3, 1, tzinfo=timezone.utc), end=datetime(2026, 4, 1, tzinfo=timezone.utc))
    assert [(item.amount, item.source) for item in january] == [(Decimal("100.00"), "ORIGINAL")]
    february = payment_financial_events(db, start=datetime(2026, 2, 1, tzinfo=timezone.utc), end=datetime(2026, 3, 1, tzinfo=timezone.utc))
    assert [(item.amount, item.source) for item in march] == [(Decimal("-100.00"), "REVERSAL")]
    assert february == ()


def test_unsupported_settlement_version_is_not_silently_estimated():
    db = contribution_db()
    _admin, _contribution, _payment, settlement = setup_contribution(db, suffix="h2b1-unsupported")
    settlement.receipt_version = "v99"
    assert payment_financial_events(db) == ()
    db.rollback()


def test_month_window_is_utc_half_open_at_boundaries():
    db = contribution_db()
    _admin, _contribution, _payment, settlement = setup_contribution(db, suffix="h2b1-boundary")
    settlement.confirmed_at = datetime(2027, 3, 1, 0, 0, tzinfo=timezone.utc)
    db.commit()
    march = payment_financial_events(
        db,
        start=datetime(2027, 3, 1, 0, 0, tzinfo=timezone.utc),
        end=datetime(2027, 4, 1, 0, 0, tzinfo=timezone.utc),
    )
    april = payment_financial_events(
        db,
        start=datetime(2027, 4, 1, 0, 0, tzinfo=timezone.utc),
        end=datetime(2027, 5, 1, 0, 0, tzinfo=timezone.utc),
    )
    assert len(march) == 1
    assert april == ()


def test_last_microsecond_of_month_is_included():
    db = contribution_db()
    _admin, _contribution, _payment, settlement = setup_contribution(db, suffix="h2b1-last-microsecond")
    settlement.confirmed_at = datetime(2027, 3, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
    db.commit()
    events = payment_financial_events(
        db,
        start=datetime(2027, 3, 1, tzinfo=timezone.utc),
        end=datetime(2027, 4, 1, tzinfo=timezone.utc),
    )
    assert len(events) == 1


def test_events_are_deterministic_decimal_and_read_only_without_autoflush():
    db = contribution_db()
    _admin, contribution, _payment, _settlement = setup_contribution(db, suffix="h2b1-readonly")
    member_id = contribution.member_id
    flushes = []

    @event.listens_for(db, "before_flush")
    def before_flush(*_args):
        flushes.append(True)

    pending = Payment(
        provider="test",
        provider_payment_id="pending-h2b1",
        idempotency_key="pending-h2b1",
        amount=Decimal("1.00"),
        status="PENDING",
    )
    db.add(pending)
    first = payment_financial_events(db, member_id=member_id)
    second = payment_financial_events(db, member_id=member_id)
    assert first == second
    assert all(isinstance(item.amount, Decimal) for item in first)
    assert flushes == []
    assert pending.id is None
    db.rollback()


def test_event_ids_are_stable_and_financial_rows_are_unchanged():
    db = contribution_db()
    admin, _member, _loan, _installment, payment, settlement = _loan_payment(
        db, "h2b1-immutable", amount="130.00", interest="20.00", penalty="10.00"
    )
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno H2B1")
    db.commit()
    payment_before = (payment.status, payment.amount_received, payment.confirmed_at)
    settlement_before = (settlement.receipt_hash, settlement.receipt_snapshot_json, settlement.amount_applied)
    ledger_before = [(row.id, row.amount, row.reference_type, row.reversal_of_id) for row in db.query(LedgerEntry).order_by(LedgerEntry.id)]
    mfe_before = [(row.id, row.entry_type, row.direction, row.amount, row.reference_id, row.payment_reversal_id) for row in db.query(MemberFinancialEntry).order_by(MemberFinancialEntry.id)]
    first = payment_financial_events(db)
    second = payment_financial_events(db)
    assert [item.event_id for item in first] == [item.event_id for item in second]
    assert len({item.event_id for item in first}) == len(first)
    assert (payment.status, payment.amount_received, payment.confirmed_at) == payment_before
    assert (settlement.receipt_hash, settlement.receipt_snapshot_json, settlement.amount_applied) == settlement_before
    assert ledger_before == [(row.id, row.amount, row.reference_type, row.reversal_of_id) for row in db.query(LedgerEntry).order_by(LedgerEntry.id)]
    assert mfe_before == [(row.id, row.entry_type, row.direction, row.amount, row.reference_id, row.payment_reversal_id) for row in db.query(MemberFinancialEntry).order_by(MemberFinancialEntry.id)]
    assert reversal.id in {item.payment_reversal_id for item in first if item.source == "REVERSAL"}
