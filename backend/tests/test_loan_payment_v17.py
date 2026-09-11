from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import (
    Group,
    LedgerEntry,
    Loan,
    LoanInstallment,
    Member,
    MemberFinancialEntry,
    Payment,
    User,
)
from app.core.security import hash_password
from app.services.loan_payments_v17 import apply_confirmed_payment
from app.services.loan_engine_v17 import release_loan
from app.services.member_financial import add_member_financial_entry, get_member_financial_position


engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = sessionmaker(bind=engine)
Base.metadata.create_all(engine)


def test_confirmed_payment_splits_principal_and_interest():
    db = TestingSessionLocal()

    try:
        user = User(
            name="Membro V17",
            email="v17@example.com",
            cpf="99999999999",
            password_hash=hash_password("Teste123!"),
            role="USER",
            is_active=True,
        )
        db.add(user)
        db.flush()

        group = Group(name="Grupo V17")
        db.add(group)
        db.flush()

        member = Member(
            user_id=user.id,
            group_id=group.id,
            status="ACTIVE",
        )
        db.add(member)
        db.flush()

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
            due_date=date.today(),
            principal=Decimal("16.67"),
            interest=Decimal("20.00"),
            amount=Decimal("36.67"),
            paid_amount=Decimal("0.00"),
            penalty_amount=Decimal("0.00"),
            paid_penalty_amount=Decimal("0.00"),
            status="OPEN",
        )
        db.add(installment)
        db.flush()

        payment = Payment(
            provider="TEST",
            provider_payment_id="TEST-V17-001",
            idempotency_key="TEST-V17-IDEMP-001",
            amount=Decimal("36.67"),
            status="APPROVED",
            reference_type="LOAN_INSTALLMENT",
            reference_id=str(installment.id),
        )
        db.add(payment)
        db.flush()

        assert apply_confirmed_payment(db, payment, installment) is True

        db.flush()

        member_entries = (
            db.query(MemberFinancialEntry)
            .filter(
                MemberFinancialEntry.reference_type
                == "LOAN_PRINCIPAL_PAYMENT",
                MemberFinancialEntry.reference_id == str(payment.id),
            )
            .all()
        )

        assert len(member_entries) == 1
        assert member_entries[0].entry_type == "LOAN_PRINCIPAL_PAYMENT"
        assert member_entries[0].direction == "CREDIT"
        assert member_entries[0].amount == Decimal("16.67")

        ledger_entries = (
            db.query(LedgerEntry)
            .filter(
                LedgerEntry.reference_type == "LOAN_INTEREST_PAYMENT",
                LedgerEntry.reference_id == str(payment.id),
            )
            .all()
        )

        assert len(ledger_entries) == 1
        assert ledger_entries[0].account == "CAIXINHA"
        assert ledger_entries[0].direction == "CREDIT"
        assert ledger_entries[0].amount == Decimal("20.00")

    finally:
        db.close()


def test_confirmed_payment_partial_allocates_interest_before_principal():
    db = TestingSessionLocal()
    try:
        user = User(
            name="Membro V17 Parcial",
            email="v17-partial@example.com",
            cpf="88888888888",
            password_hash=hash_password("Teste123!"),
            role="USER",
            is_active=True,
        )
        db.add(user)
        db.flush()

        group = Group(name="Grupo V17 Parcial")
        db.add(group)
        db.flush()

        member = Member(
            user_id=user.id,
            group_id=group.id,
            status="ACTIVE",
        )
        db.add(member)
        db.flush()

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
            due_date=date.today(),
            principal=Decimal("16.67"),
            interest=Decimal("20.00"),
            amount=Decimal("36.67"),
            paid_amount=Decimal("0.00"),
            penalty_amount=Decimal("0.00"),
            paid_penalty_amount=Decimal("0.00"),
            status="OPEN",
        )
        db.add(installment)
        db.flush()

        # Primeiro pagamento: cobre somente parte dos juros.
        payment1 = Payment(
            provider="TEST",
            provider_payment_id="TEST-V17-PARTIAL-001",
            idempotency_key="TEST-V17-PARTIAL-IDEMP-001",
            amount=Decimal("10.00"),
            status="APPROVED",
            reference_type="LOAN_INSTALLMENT",
            reference_id=str(installment.id),
        )
        db.add(payment1)
        db.flush()

        assert apply_confirmed_payment(db, payment1, installment) is True
        db.flush()

        principal_entries = db.query(MemberFinancialEntry).filter(
            MemberFinancialEntry.reference_type == "LOAN_PRINCIPAL_PAYMENT",
            MemberFinancialEntry.reference_id == str(payment1.id),
        ).all()

        assert len(principal_entries) == 0

        interest_entries = db.query(LedgerEntry).filter(
            LedgerEntry.reference_type == "LOAN_INTEREST_PAYMENT",
            LedgerEntry.reference_id == str(payment1.id),
        ).all()

        assert len(interest_entries) == 1
        assert interest_entries[0].amount == Decimal("10.00")

        # Segundo pagamento: quita os R$ 10,00 restantes de juros
        # e devolve R$ 16,67 de principal ao saldo próprio.
        payment2 = Payment(
            provider="TEST",
            provider_payment_id="TEST-V17-PARTIAL-002",
            idempotency_key="TEST-V17-PARTIAL-IDEMP-002",
            amount=Decimal("26.67"),
            status="APPROVED",
            reference_type="LOAN_INSTALLMENT",
            reference_id=str(installment.id),
        )
        db.add(payment2)
        db.flush()

        assert apply_confirmed_payment(db, payment2, installment) is True
        db.flush()

        principal_entries = db.query(MemberFinancialEntry).filter(
            MemberFinancialEntry.reference_type == "LOAN_PRINCIPAL_PAYMENT",
            MemberFinancialEntry.reference_id == str(payment2.id),
        ).all()

        assert len(principal_entries) == 1
        assert principal_entries[0].amount == Decimal("16.67")

        interest_entries = db.query(LedgerEntry).filter(
            LedgerEntry.reference_type == "LOAN_INTEREST_PAYMENT",
            LedgerEntry.reference_id == str(payment2.id),
        ).all()

        assert len(interest_entries) == 1
        assert interest_entries[0].amount == Decimal("10.00")

        assert installment.status == "PAID"
        assert installment.paid_amount == Decimal("36.67")

    finally:
        db.close()


def test_confirmed_payment_is_idempotent_for_financial_split():
    db = TestingSessionLocal()
    try:
        user = User(
            name="Membro V17 Idempotencia",
            email="v17-idempotency@example.com",
            cpf="77777777777",
            password_hash=hash_password("Teste123!"),
            role="USER",
            is_active=True,
        )
        db.add(user)
        db.flush()

        group = Group(name="Grupo V17 Idempotencia")
        db.add(group)
        db.flush()

        member = Member(
            user_id=user.id,
            group_id=group.id,
            status="ACTIVE",
        )
        db.add(member)
        db.flush()

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
            due_date=date.today(),
            principal=Decimal("16.67"),
            interest=Decimal("20.00"),
            amount=Decimal("36.67"),
            paid_amount=Decimal("0.00"),
            penalty_amount=Decimal("0.00"),
            paid_penalty_amount=Decimal("0.00"),
            status="OPEN",
        )
        db.add(installment)
        db.flush()

        payment = Payment(
            provider="TEST",
            provider_payment_id="TEST-V17-IDEMP-001",
            idempotency_key="TEST-V17-IDEMP-001",
            amount=Decimal("36.67"),
            status="APPROVED",
            reference_type="LOAN_INSTALLMENT",
            reference_id=str(installment.id),
        )
        db.add(payment)
        db.flush()

        assert apply_confirmed_payment(db, payment, installment) is True
        db.flush()

        # Segunda tentativa do mesmo pagamento.
        assert apply_confirmed_payment(db, payment, installment) is False
        db.flush()

        principal_entries = db.query(MemberFinancialEntry).filter(
            MemberFinancialEntry.reference_type == "LOAN_PRINCIPAL_PAYMENT",
            MemberFinancialEntry.reference_id == str(payment.id),
        ).all()

        assert len(principal_entries) == 1
        assert principal_entries[0].amount == Decimal("16.67")

        interest_entries = db.query(LedgerEntry).filter(
            LedgerEntry.reference_type == "LOAN_INTEREST_PAYMENT",
            LedgerEntry.reference_id == str(payment.id),
        ).all()

        assert len(interest_entries) == 1
        assert interest_entries[0].amount == Decimal("20.00")

        assert installment.paid_amount == Decimal("36.67")
        assert installment.status == "PAID"

    finally:
        db.close()


def test_release_loan_creates_principal_commitment_idempotently():
    db = TestingSessionLocal()

    try:
        user = User(
            name="Membro Commitment",
            email="commitment@example.com",
            cpf="77777777777",
            password_hash=hash_password("Teste123!"),
            role="USER",
            is_active=True,
        )
        db.add(user)
        db.flush()

        group = Group(name="Grupo Commitment")
        db.add(group)
        db.flush()

        member = Member(
            user_id=user.id,
            group_id=group.id,
            status="ACTIVE",
        )
        db.add(member)
        db.flush()

        # O participante possui R$ 150,00 de saldo próprio.
        add_member_financial_entry(
            db=db,
            member=member,
            entry_type="CONTRIBUTION",
            direction="CREDIT",
            amount=Decimal("150.00"),
            reference_type="CONTRIBUTION",
            reference_id="commitment-test-contribution",
            description="Saldo próprio inicial para teste.",
        )
        db.flush()

        loan = Loan(
            member_id=member.id,
            principal=Decimal("100.00"),
            monthly_rate=Decimal("0.20"),
            installments=1,
            status="APPROVED",
        )
        db.add(loan)
        db.flush()

        # Antes da liberação:
        position = get_member_financial_position(db, member)

        assert position["own_balance"] == Decimal("150.00")
        assert position["committed_balance"] == Decimal("0.00")
        assert position["available_balance"] == Decimal("150.00")

        # Primeira liberação.
        assert release_loan(db, loan, user.id) is True
        db.flush()

        position = get_member_financial_position(db, member)

        assert position["own_balance"] == Decimal("150.00")
        assert position["committed_balance"] == Decimal("100.00")
        assert position["available_balance"] == Decimal("50.00")

        # Segunda chamada não pode duplicar o compromisso.
        assert release_loan(db, loan, user.id) is False
        db.flush()

        position = get_member_financial_position(db, member)

        assert position["own_balance"] == Decimal("150.00")
        assert position["committed_balance"] == Decimal("100.00")
        assert position["available_balance"] == Decimal("50.00")

        commitment_entries = (
            db.query(MemberFinancialEntry)
            .filter(
                MemberFinancialEntry.entry_type
                == "LOAN_PRINCIPAL_COMMITMENT",
                MemberFinancialEntry.reference_type
                == "LOAN_PRINCIPAL_COMMITMENT",
                MemberFinancialEntry.reference_id == str(loan.id),
            )
            .all()
        )

        assert len(commitment_entries) == 1
        assert commitment_entries[0].direction == "CREDIT"
        assert commitment_entries[0].amount == Decimal("100.00")

    finally:
        db.close()
