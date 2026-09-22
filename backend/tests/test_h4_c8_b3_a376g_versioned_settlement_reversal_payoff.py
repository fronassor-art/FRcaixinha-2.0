from datetime import date, datetime, timezone
from decimal import Decimal
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.loan_rules import (
    LATE_CHARGE_SETTLEMENT_COMPONENT_VERSION,
    LATE_CHARGE_VERSION,
)
from app.db.base import Base
from app.models import (
    Group,
    LedgerEntry,
    Loan,
    LoanInstallment,
    LoanLateChargeEvent,
    Member,
    MemberFinancialEntry,
    Payment,
    PaymentReversal,
    PaymentSettlement,
    User,
)
from app.services import payment_settlement as settlement_service
from app.services.loan_obligation_runtime import (
    loan_payoff_quote,
    materialize_daily_late_interest,
    materialize_fixed_penalty,
)
from app.services.loan_payments_v17 import settle_loan_with_own_balance
from app.services.member_financial import add_member_financial_entry
from app.services.payment_reversal import reverse_payment
from app.services.payment_settlement import settle_confirmed_pix_payment


UTC = timezone.utc
PRICE_ROWS = (
    ("15.11", "30.00", "45.11"),
    ("18.13", "26.98", "45.11"),
    ("21.76", "23.35", "45.11"),
    ("26.11", "19.00", "45.11"),
    ("31.33", "13.78", "45.11"),
    ("37.56", "7.51", "45.07"),
)


def _db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _member(db, suffix):
    group = Group(name=f"A376G {suffix}", max_installments=6)
    user = User(
        name=f"Member {suffix}",
        email=f"{suffix}@a376g.test",
        cpf=f"cpf-{suffix}",
        password_hash="x",
    )
    db.add_all([group, user])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id)
    db.add(member)
    db.flush()
    return member


def _admin(db, suffix):
    user = User(
        name=f"Admin {suffix}",
        email=f"admin-{suffix}@a376g.test",
        cpf=f"admin-cpf-{suffix}",
        password_hash="x",
        role="ADMIN",
        is_active=True,
        is_master=True,
    )
    db.add(user)
    db.flush()
    return user


def _single_installment(db, suffix, *, due=date(2026, 1, 1)):
    member = _member(db, suffix)
    loan = Loan(
        member_id=member.id,
        principal=Decimal("100.00"),
        monthly_rate=Decimal("0.20"),
        installments=1,
        calculation_version="price_amortization_v1",
        status="ACTIVE",
    )
    db.add(loan)
    db.flush()
    installment = LoanInstallment(
        loan_id=loan.id,
        number=1,
        due_date=due,
        principal=Decimal("100.00"),
        interest=Decimal("20.00"),
        amount=Decimal("120.00"),
        paid_amount=Decimal("0.00"),
        penalty_amount=Decimal("0.00"),
        paid_penalty_amount=Decimal("0.00"),
        status="OPEN",
    )
    db.add(installment)
    db.flush()
    return member, loan, installment


def _payment(db, installment, suffix, amount, *, confirmed_at=None):
    payment = Payment(
        provider="a376g",
        provider_payment_id=f"provider-{suffix}",
        idempotency_key=f"key-{suffix}",
        amount=Decimal(amount),
        amount_received=Decimal(amount),
        status="approved",
        raw_status="approved",
        reference_type="LOAN_INSTALLMENT",
        reference_id=str(installment.id),
        confirmed_at=confirmed_at,
    )
    db.add(payment)
    db.flush()
    return payment


@pytest.mark.parametrize(
    "received,expected",
    (
        ("5.00", ("5.00", "0.00", "0.00", "0.00", "5.00", "0.00")),
        ("10.00", ("10.00", "0.00", "0.00", "0.00", "10.00", "0.00")),
        ("10.50", ("10.00", "0.50", "0.00", "0.00", "10.50", "0.00")),
        ("12.00", ("10.00", "1.33", "0.67", "0.00", "12.00", "0.00")),
        ("31.33", ("10.00", "1.33", "20.00", "0.00", "31.33", "0.00")),
        ("41.33", ("10.00", "1.33", "20.00", "10.00", "41.33", "0.00")),
        ("131.33", ("10.00", "1.33", "20.00", "100.00", "131.33", "0.00")),
        ("140.00", ("10.00", "1.33", "20.00", "100.00", "131.33", "8.67")),
    ),
)
def test_versioned_allocation_order_and_equations(monkeypatch, received, expected):
    monkeypatch.setattr(settings, "loan_late_charge_effective_date", date(2026, 1, 1))
    db = _db()
    _, _, installment = _single_installment(db, f"alloc-{received}")
    confirmed = datetime(2026, 1, 3, 15, tzinfo=UTC)
    payment = _payment(db, installment, f"alloc-{received}", received)

    settlement = settle_confirmed_pix_payment(
        db, payment, confirmation_source="TEST_PROVIDER", confirmed_at=confirmed
    )
    fixed, late, normal, principal, applied, excess = map(Decimal, expected)
    assert settlement.settlement_component_version == LATE_CHARGE_SETTLEMENT_COMPONENT_VERSION
    assert settlement.fixed_penalty_applied == fixed
    assert settlement.late_interest_applied == late
    assert settlement.normal_interest_applied == normal
    assert settlement.principal_applied == principal
    assert settlement.interest_applied == normal
    assert settlement.penalty_applied == fixed + late
    assert settlement.amount_applied == applied
    assert settlement.excess_amount == excess
    assert settlement.amount_received == settlement.amount_applied + settlement.excess_amount
    assert settlement.amount_applied == (
        settlement.principal_applied
        + settlement.interest_applied
        + settlement.penalty_applied
    )
    snapshot = json.loads(settlement.receipt_snapshot_json)
    assert snapshot["amounts"]["normal_price_interest"] == format(normal, "f")
    assert snapshot["amounts"]["late_interest"] == format(late, "f")
    assert snapshot["amounts"]["fixed_penalty"] == format(fixed, "f")
    assert installment.paid_amount == normal + principal
    assert installment.paid_penalty_amount == fixed + late
    assert installment.paid_fixed_penalty_amount == fixed
    assert installment.paid_late_interest_amount == late
    db.close()


def test_versioned_confirmed_at_is_explicit_aware_and_belem_based(monkeypatch):
    monkeypatch.setattr(settings, "loan_late_charge_effective_date", date(2026, 1, 1))
    db = _db()
    _, _, installment = _single_installment(db, "trusted")
    payment = _payment(
        db,
        installment,
        "trusted",
        "131.33",
        confirmed_at=datetime(2026, 1, 3, 12, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="explícito"):
        settle_confirmed_pix_payment(db, payment, confirmation_source="TEST")
    with pytest.raises(ValueError, match="timezone-aware"):
        settle_confirmed_pix_payment(
            db,
            payment,
            confirmation_source="TEST",
            confirmed_at=datetime(2026, 1, 3, 12),
        )

    # 02:30 UTC is still January 3 in America/Belem: two overdue days.
    settlement = settle_confirmed_pix_payment(
        db,
        payment,
        confirmation_source="TEST",
        confirmed_at=datetime(2026, 1, 4, 2, 30, tzinfo=UTC),
    )
    assert settlement.late_interest_applied == Decimal("1.33")
    db.close()


def test_writer_connects_settlement_and_reversal_adjustments(monkeypatch):
    monkeypatch.setattr(settings, "loan_late_charge_effective_date", date(2026, 1, 1))
    db = _db()
    _, _, installment = _single_installment(db, "adjustments")
    materialize_fixed_penalty(
        db,
        installment.id,
        through_date=date(2026, 1, 5),
        late_charge_effective_date=date(2026, 1, 1),
    )
    materialize_daily_late_interest(
        db,
        installment.id,
        through_date=date(2026, 1, 5),
        late_charge_effective_date=date(2026, 1, 1),
    )
    payment = _payment(db, installment, "adjustments", "71.33")
    settlement = settle_confirmed_pix_payment(
        db,
        payment,
        confirmation_source="TEST",
        confirmed_at=datetime(2026, 1, 3, 12, tzinfo=UTC),
    )
    decrease = db.query(LoanLateChargeEvent).filter_by(
        payment_settlement_id=settlement.id
    ).one()
    assert decrease.event_type == "LATE_INTEREST_ADJUSTMENT_DECREASE"
    assert decrease.effective_date == date(2026, 1, 4)
    assert decrease.amount == Decimal("0.54")
    assert installment.late_interest_accrued_through_date == date(2026, 1, 5)

    admin = _admin(db, "adjustments")
    reversal = reverse_payment(
        db,
        payment_id=payment.id,
        admin_id=admin.id,
        reason="Reversão versionada válida",
        now=datetime(2026, 1, 4, 12, tzinfo=UTC),
    )
    increase = db.query(LoanLateChargeEvent).filter_by(
        payment_reversal_id=reversal.id
    ).one()
    assert increase.event_type == "LATE_INTEREST_ADJUSTMENT_INCREASE"
    assert increase.effective_date == date(2026, 1, 5)
    assert increase.amount == Decimal("0.27")
    assert db.query(PaymentReversal).count() == 1
    assert installment.paid_fixed_penalty_amount == Decimal("0.00")
    assert installment.paid_late_interest_amount == Decimal("0.00")
    db.close()


def test_settlement_adjustment_failure_rolls_back_caller_transaction(monkeypatch):
    monkeypatch.setattr(settings, "loan_late_charge_effective_date", date(2026, 1, 1))
    db = _db()
    _, loan, installment = _single_installment(db, "rollback")
    payment = _payment(db, installment, "rollback", "131.33")
    db.commit()

    def fail(*args, **kwargs):
        raise RuntimeError("adjustment failure")

    monkeypatch.setattr(
        settlement_service,
        "materialize_settlement_late_interest_adjustment",
        fail,
    )
    with pytest.raises(RuntimeError, match="adjustment failure"):
        settle_confirmed_pix_payment(
            db,
            payment,
            confirmation_source="TEST",
            confirmed_at=datetime(2026, 1, 3, 12, tzinfo=UTC),
        )
    db.rollback()
    db.refresh(loan)
    db.refresh(installment)
    assert db.query(PaymentSettlement).count() == 0
    assert db.query(LedgerEntry).count() == 0
    assert db.query(MemberFinancialEntry).count() == 0
    assert installment.paid_amount == Decimal("0.00")
    assert loan.state_revision == 0
    db.close()


def _price_loan(db, suffix):
    member = _member(db, suffix)
    loan = Loan(
        member_id=member.id,
        principal=Decimal("150.00"),
        monthly_rate=Decimal("0.20"),
        installments=6,
        calculation_version="price_amortization_v1",
        status="ACTIVE",
    )
    db.add(loan)
    db.flush()
    due_dates = (
        date(2026, 2, 1),
        date(2026, 3, 1),
        date(2026, 4, 1),
        date(2026, 5, 1),
        date(2026, 6, 1),
        date(2026, 7, 1),
    )
    installments = []
    for number, ((principal, interest, amount), due) in enumerate(
        zip(PRICE_ROWS, due_dates), start=1
    ):
        row = LoanInstallment(
            loan_id=loan.id,
            number=number,
            due_date=due,
            principal=Decimal(principal),
            interest=Decimal(interest),
            amount=Decimal(amount),
            paid_amount=Decimal("0.00"),
            penalty_amount=Decimal("0.00"),
            paid_penalty_amount=Decimal("0.00"),
            status="OPEN",
        )
        db.add(row)
        installments.append(row)
    db.flush()
    return member, loan, installments


def test_payoff_model_b_dates_partial_payment_reversal_and_no_future_price(monkeypatch):
    monkeypatch.setattr(settings, "loan_late_charge_effective_date", date(2026, 1, 1))
    db = _db()
    _, loan, installments = _price_loan(db, "payoff")
    event_count = db.query(LoanLateChargeEvent).count()

    before_due = loan_payoff_quote(db, loan, date(2026, 1, 31))
    assert before_due.principal_due == Decimal("150.00")
    assert before_due.normal_price_interest_due == Decimal("0.00")
    assert before_due.total_due == Decimal("150.00")
    assert db.query(LoanLateChargeEvent).count() == event_count

    on_due = loan_payoff_quote(db, loan, date(2026, 2, 1))
    assert on_due.principal_due == Decimal("150.00")
    assert on_due.normal_price_interest_due == Decimal("30.00")
    assert on_due.total_due == Decimal("180.00")
    assert on_due.normal_price_interest_due != Decimal("120.62")

    payment = _payment(db, installments[0], "payoff-partial", "35.00")
    settlement = settle_confirmed_pix_payment(
        db,
        payment,
        confirmation_source="TEST",
        confirmed_at=datetime(2026, 2, 1, 15, tzinfo=UTC),
    )
    partial = loan_payoff_quote(db, loan, date(2026, 2, 1))
    assert settlement.normal_interest_applied == Decimal("30.00")
    assert settlement.principal_applied == Decimal("5.00")
    assert partial.principal_due == Decimal("145.00")
    assert partial.normal_price_interest_due == Decimal("0.00")
    assert partial.total_due == Decimal("145.00")

    admin = _admin(db, "payoff")
    reverse_payment(
        db,
        payment_id=payment.id,
        admin_id=admin.id,
        reason="Reversão para quote",
        now=datetime(2026, 2, 1, 18, tzinfo=UTC),
    )
    reversed_quote = loan_payoff_quote(db, loan, date(2026, 2, 1))
    assert reversed_quote.principal_due == Decimal("150.00")
    assert reversed_quote.normal_price_interest_due == Decimal("30.00")
    assert reversed_quote.total_due == Decimal("180.00")
    db.close()


def test_payoff_after_due_uses_only_materialized_charges_and_quote_has_no_writes(monkeypatch):
    monkeypatch.setattr(settings, "loan_late_charge_effective_date", date(2026, 1, 1))
    db = _db()
    _, loan, installments = _price_loan(db, "payoff-overdue")
    materialize_fixed_penalty(
        db,
        installments[0].id,
        through_date=date(2026, 2, 2),
        late_charge_effective_date=date(2026, 1, 1),
    )
    materialize_daily_late_interest(
        db,
        installments[0].id,
        through_date=date(2026, 2, 2),
        late_charge_effective_date=date(2026, 1, 1),
    )
    before = db.query(LoanLateChargeEvent).count()
    quote = loan_payoff_quote(db, loan, date(2026, 2, 2))
    assert quote.principal_due == Decimal("150.00")
    assert quote.normal_price_interest_due == Decimal("30.00")
    assert quote.fixed_penalty_due == Decimal("10.00")
    assert quote.late_interest_due == Decimal("0.10")
    assert quote.total_due == Decimal("190.10")
    assert db.query(LoanLateChargeEvent).count() == before
    db.close()


def test_legacy_v4_remains_unversioned_and_reversible(monkeypatch):
    monkeypatch.setattr(settings, "loan_late_charge_effective_date", None)
    db = _db()
    _, _, installment = _single_installment(
        db, "legacy", due=date(2026, 12, 1)
    )
    payment = _payment(db, installment, "legacy", "120.00")
    settlement = settle_confirmed_pix_payment(
        db,
        payment,
        confirmation_source="LEGACY_TEST",
        confirmed_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
    )
    assert settlement.receipt_version == "v4"
    assert settlement.settlement_component_version is None
    assert settlement.normal_interest_applied is None
    admin = _admin(db, "legacy")
    reversal = reverse_payment(
        db,
        payment_id=payment.id,
        admin_id=admin.id,
        reason="Reversão legacy válida",
        now=datetime(2026, 1, 2, 12, tzinfo=UTC),
    )
    assert reversal.id is not None
    assert installment.paid_amount == Decimal("0.00")
    db.close()


def test_own_balance_payoff_uses_remaining_principal_without_double_counting(monkeypatch):
    monkeypatch.setattr(settings, "loan_late_charge_effective_date", date(2026, 1, 1))
    db = _db()
    member, loan, installments = _price_loan(db, "own-principal")
    payment = _payment(db, installments[0], "own-principal", "45.11")
    settle_confirmed_pix_payment(
        db,
        payment,
        confirmation_source="TEST",
        confirmed_at=datetime(2026, 2, 1, 15, tzinfo=UTC),
    )
    add_member_financial_entry(
        db,
        member,
        entry_type="CONTRIBUTION",
        direction="CREDIT",
        amount=Decimal("300.00"),
        reference_type="CONTRIBUTION",
        reference_id="own-principal-funds",
        description="Fundos próprios para payoff.",
    )

    result = settle_loan_with_own_balance(
        db,
        loan,
        actor_id=member.user_id,
        financial_at=datetime(2026, 2, 1, 18, tzinfo=UTC),
    )
    assert result["settlement_amount"] == Decimal("134.89")
    assert loan.principal_settled_with_own_balance == Decimal("134.89")
    assert db.query(MemberFinancialEntry).filter_by(
        entry_type="OWN_BALANCE_SETTLEMENT",
        reference_id=str(loan.id),
    ).one().amount == Decimal("134.89")
    assert loan.status == "PAID"
    db.close()


def test_own_balance_payoff_fails_closed_when_price_charge_is_due(monkeypatch):
    monkeypatch.setattr(settings, "loan_late_charge_effective_date", date(2026, 1, 1))
    db = _db()
    member, loan, _ = _price_loan(db, "own-open")
    add_member_financial_entry(
        db,
        member,
        entry_type="CONTRIBUTION",
        direction="CREDIT",
        amount=Decimal("300.00"),
        reference_type="CONTRIBUTION",
        reference_id="own-open-funds",
        description="Fundos próprios para teste.",
    )
    with pytest.raises(ValueError, match="BUSINESS_DECISION_REQUIRED"):
        settle_loan_with_own_balance(
            db,
            loan,
            actor_id=member.user_id,
            financial_at=datetime(2026, 2, 1, 15, tzinfo=UTC),
        )
    assert db.query(MemberFinancialEntry).filter_by(
        entry_type="OWN_BALANCE_SETTLEMENT"
    ).count() == 0
    assert loan.status == "ACTIVE"
    db.close()
