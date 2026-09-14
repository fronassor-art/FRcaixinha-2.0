from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import AgreementInstallment, CollectionAgreement, Contribution, Group, LedgerEntry, Loan, LoanInstallment, Member, Payment, User
from app.services.ledger import post_entry, reverse_entry
from app.services.payment_settlement import settle_confirmed_pix_payment
from app.services.reports_v10 import member_statement


def make_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def make_member(db, suffix="one"):
    group = Group(name=f"Grupo {suffix}", max_installments=6)
    user = User(name=f"Membro {suffix}", email=f"{suffix}@test", cpf=f"cpf-{suffix}", password_hash="x")
    db.add_all([group, user])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id)
    db.add(member)
    db.flush()
    return member


def make_payment(db, suffix, amount, reference_type, reference_id):
    payment = Payment(
        provider="mercado_pago",
        provider_payment_id=f"provider-{suffix}",
        idempotency_key=f"idempotency-{suffix}",
        amount=Decimal(amount),
        status="approved",
        reference_type=reference_type,
        reference_id=str(reference_id),
    )
    db.add(payment)
    db.flush()
    return payment


def settle(db, payment, when):
    settle_confirmed_pix_payment(
        db,
        payment,
        confirmation_source="WEBHOOK",
        confirmed_at=when,
    )
    db.commit()


def make_installment(db, member, *, interest="20.00", penalty="0.00"):
    loan = Loan(
        member_id=member.id,
        principal=Decimal("100.00"),
        monthly_rate=Decimal("0.20"),
        installments=1,
        status="ACTIVE",
    )
    db.add(loan)
    db.flush()
    installment = LoanInstallment(
        loan_id=loan.id,
        number=1,
        due_date=date(2026, 2, 10),
        principal=Decimal("100.00"),
        interest=Decimal(interest),
        amount=Decimal("100.00") + Decimal(interest),
        penalty_amount=Decimal(penalty),
        status="OPEN",
    )
    db.add(installment)
    db.flush()
    return loan, installment


def by_payment(statement):
    return {row["payment_id"]: row for row in statement["movements"] if row["payment_id"] is not None}


def test_statement_keeps_legacy_contract_and_has_no_movements_for_pending_obligations():
    db = make_db()
    member = make_member(db)
    contribution = Contribution(
        member_id=member.id,
        competence=date(2026, 1, 1),
        amount=Decimal("100.00"),
        due_date=date(2026, 1, 10),
        status="PENDING",
    )
    db.add(contribution)
    make_installment(db, member)
    db.commit()

    statement = member_statement(db, member.id)

    assert {"member", "totals", "contributions", "loans", "installments", "movements"} <= set(statement)
    assert statement["contributions"][0]["id"] == contribution.id
    assert statement["movements"] == []


def test_confirmed_contribution_is_one_movement_with_receipt_and_competence():
    db = make_db()
    member = make_member(db)
    contribution = Contribution(member_id=member.id, competence=date(2026, 3, 1), amount=Decimal("100.00"))
    db.add(contribution)
    db.flush()
    payment = make_payment(db, "contribution", "100.00", "CONTRIBUTION", contribution.id)
    confirmed_at = datetime(2026, 3, 3, 12, tzinfo=timezone.utc)
    settle(db, payment, confirmed_at)

    movement = by_payment(member_statement(db, member.id))[payment.id]

    assert movement["type"] == "CONTRIBUTION_PAYMENT"
    assert movement["direction"] == "DEBIT"
    assert movement["total"] == "100.00"
    assert movement["principal"] is None
    assert movement["competence"] == "2026-03-01"
    assert movement["receipt_available"] is True
    assert movement["occurred_at"] == confirmed_at.isoformat()


def test_multiple_partial_contribution_pix_payments_are_distinct_without_duplication():
    db = make_db()
    member = make_member(db)
    contribution = Contribution(member_id=member.id, competence=date(2026, 4, 1), amount=Decimal("100.00"))
    db.add(contribution)
    db.flush()
    first = make_payment(db, "partial-one", "40.00", "CONTRIBUTION", contribution.id)
    settle(db, first, datetime(2026, 4, 2, tzinfo=timezone.utc))

    partial_statement = member_statement(db, member.id)
    assert partial_statement["contributions"][0]["status"] == "PARTIAL"
    assert partial_statement["contributions"][0]["paid_amount"] == "40.00"
    assert [row["total"] for row in partial_statement["movements"]] == ["40.00"]

    second = make_payment(db, "partial-two", "60.00", "CONTRIBUTION", contribution.id)
    settle(db, second, datetime(2026, 4, 4, tzinfo=timezone.utc))

    movements = member_statement(db, member.id)["movements"]
    rows = [row for row in movements if row["type"] == "CONTRIBUTION_PAYMENT"]

    assert [row["payment_id"] for row in rows] == [second.id, first.id]
    assert [row["total"] for row in rows] == ["60.00", "40.00"]
    assert len({row["id"] for row in rows}) == 2
    assert sum(Decimal(row["total"]) for row in rows) == Decimal("100.00")


def test_statement_includes_posted_loan_disbursement_only_once():
    db = make_db()
    member = make_member(db)
    loan, _ = make_installment(db, member)
    post_entry(db, "CAIXINHA", "DEBIT", Decimal("100.00"), "LOAN_DISBURSEMENT", str(loan.id))
    db.commit()

    rows = [row for row in member_statement(db, member.id)["movements"] if row["type"] == "LOAN_DISBURSEMENT"]

    assert len(rows) == 1
    assert rows[0]["direction"] == "CREDIT"
    assert rows[0]["loan_id"] == loan.id
    assert rows[0]["total"] == "100.00"
    assert rows[0]["payment_id"] is None


def test_installment_principal_and_interest_are_grouped_in_one_payment_movement():
    db = make_db()
    member = make_member(db)
    loan, installment = make_installment(db, member)
    payment = make_payment(db, "loan-base", "120.00", "LOAN_INSTALLMENT", installment.id)
    settle(db, payment, datetime(2026, 5, 2, tzinfo=timezone.utc))

    rows = [row for row in member_statement(db, member.id)["movements"] if row["payment_id"] == payment.id]

    assert len(rows) == 1
    assert rows[0] == {
        "id": f"payment:{payment.id}",
        "type": "LOAN_INSTALLMENT_PAYMENT",
        "direction": "DEBIT",
        "occurred_at": "2026-05-02T00:00:00+00:00",
        "description": f"Pagamento da parcela 1 do empréstimo #{loan.id}",
        "total": "120.00",
        "competence": None,
        "loan_id": loan.id,
        "installment_number": 1,
        "principal": "100.00",
        "interest": "20.00",
        "penalty": "0.00",
        "payment_id": payment.id,
        "receipt_available": True,
    }


def test_installment_penalty_interest_and_principal_are_one_movement_without_settlement_double_count():
    db = make_db()
    member = make_member(db)
    _, installment = make_installment(db, member, penalty="10.00")
    payment = make_payment(db, "loan-penalty", "130.00", "LOAN_INSTALLMENT", installment.id)
    settle(db, payment, datetime(2026, 6, 2, tzinfo=timezone.utc))

    rows = [row for row in member_statement(db, member.id)["movements"] if row["payment_id"] == payment.id]

    assert len(rows) == 1
    assert rows[0]["total"] == "130.00"
    assert rows[0]["principal"] == "100.00"
    assert rows[0]["interest"] == "20.00"
    assert rows[0]["penalty"] == "10.00"
    assert rows[0]["receipt_available"] is True


def test_unconfirmed_payment_never_appears_as_a_movement():
    db = make_db()
    member = make_member(db)
    contribution = Contribution(member_id=member.id, competence=date(2026, 7, 1), amount=Decimal("50.00"))
    db.add(contribution)
    db.flush()
    make_payment(db, "pending", "50.00", "CONTRIBUTION", contribution.id).status = "pending"
    db.commit()

    assert member_statement(db, member.id)["movements"] == []


def test_reversal_of_member_contribution_ledger_entry_is_a_separate_movement():
    db = make_db()
    member = make_member(db)
    contribution = Contribution(member_id=member.id, competence=date(2026, 8, 1), amount=Decimal("50.00"))
    db.add(contribution)
    db.flush()
    payment = make_payment(db, "reversal", "50.00", "CONTRIBUTION", contribution.id)
    settlement = settle_confirmed_pix_payment(db, payment, confirmation_source="WEBHOOK")
    original = db.query(LedgerEntry).filter(LedgerEntry.reference_id == str(payment.id)).one()
    reverse_entry(db, original, "Estorno de teste válido")
    db.commit()

    rows = [row for row in member_statement(db, member.id)["movements"] if row["payment_id"] == payment.id]

    assert settlement is not None
    assert [row["type"] for row in rows] == ["REVERSAL", "CONTRIBUTION_PAYMENT"]
    assert rows[0]["direction"] == "CREDIT"
    assert rows[0]["total"] == "50.00"


def test_reversal_without_a_canonical_member_link_is_not_projected():
    db = make_db()
    member = make_member(db, "visible")
    other = make_member(db, "other")
    contribution = Contribution(member_id=other.id, competence=date(2026, 9, 1), amount=Decimal("10.00"))
    db.add(contribution)
    db.flush()
    payment = make_payment(db, "foreign-reversal", "10.00", "CONTRIBUTION", contribution.id)
    settle(db, payment, datetime(2026, 9, 2, tzinfo=timezone.utc))
    original = db.query(LedgerEntry).filter(LedgerEntry.reference_id == str(payment.id)).one()
    reverse_entry(db, original, "Estorno estrangeiro válido")
    db.commit()

    assert member_statement(db, member.id)["movements"] == []


def test_legacy_agreement_payment_requires_its_canonical_member_link():
    db = make_db()
    member = make_member(db, "agreement")
    other = make_member(db, "agreement-other")
    loan, _ = make_installment(db, member)
    agreement = CollectionAgreement(member_id=member.id, loan_id=loan.id, requested_by=member.user_id, installments=1, total_amount=Decimal("25.00"), snapshot="{}")
    db.add(agreement)
    db.flush()
    installment = AgreementInstallment(agreement_id=agreement.id, number=1, due_date=date(2026, 10, 10), principal=Decimal("25.00"), amount=Decimal("25.00"))
    db.add(installment)
    db.flush()
    payment = make_payment(db, "agreement-own", "25.00", "AGREEMENT_INSTALLMENT", installment.id)
    post_entry(db, "CAIXINHA", "CREDIT", Decimal("25.00"), "AGREEMENT_INSTALLMENT_PAYMENT", str(payment.id))
    db.commit()

    own = [row for row in member_statement(db, member.id)["movements"] if row["payment_id"] == payment.id]
    foreign = [row for row in member_statement(db, other.id)["movements"] if row["payment_id"] == payment.id]

    assert len(own) == 1
    assert own[0]["direction"] == "DEBIT"
    assert foreign == []


def test_occurred_at_falls_back_from_settlement_to_payment_then_ledger_timestamp():
    db = make_db()
    member = make_member(db, "dates")
    contribution = Contribution(member_id=member.id, competence=date(2026, 11, 1), amount=Decimal("30.00"))
    db.add(contribution)
    db.flush()
    payment = make_payment(db, "payment-date", "10.00", "CONTRIBUTION", contribution.id)
    payment.confirmed_at = datetime(2026, 11, 3, tzinfo=timezone.utc)
    ledger = post_entry(db, "CAIXINHA", "CREDIT", Decimal("10.00"), "CONTRIBUTION_PAYMENT", str(payment.id))
    db.flush()
    payment_fallback = by_payment(member_statement(db, member.id))[payment.id]
    assert payment_fallback["occurred_at"] == payment.confirmed_at.isoformat()

    other_contribution = Contribution(member_id=member.id, competence=date(2026, 12, 1), amount=Decimal("20.00"))
    db.add(other_contribution)
    db.flush()
    ledger_payment = make_payment(db, "ledger-date", "20.00", "CONTRIBUTION", other_contribution.id)
    ledger_fallback = post_entry(db, "CAIXINHA", "CREDIT", Decimal("20.00"), "CONTRIBUTION_PAYMENT", str(ledger_payment.id))
    db.flush()

    movement = by_payment(member_statement(db, member.id))[ledger_payment.id]
    assert ledger is not None
    assert movement["occurred_at"] == ledger_fallback.created_at.astimezone(timezone.utc).isoformat()
