from datetime import date, timedelta
from decimal import Decimal

from app.db.session import SessionLocal
from app.models import (
    User,
    Group,
    Member,
    Quota,
    Contribution,
    Payment,
    Loan,
    LoanInstallment,
    AuditLog,
)
from app.services.ledger import post_entry
from app.services.member_financial import add_member_financial_entry
from app.services.loan_amortization import calculate_linear_amortization
from app.services.loan_engine_v17 import add_months


TEST_PASSWORD = "Homologacao123!"
GROUP_NAME = "FRcaixinha HOMOLOGAÇÃO 2026"
CONTRIBUTION_AMOUNT = Decimal("150.00")


def get_or_create_group(db):
    group = db.query(Group).filter(Group.name == GROUP_NAME).first()

    if group is None:
        group = Group(
            name=GROUP_NAME,
            monthly_amount=CONTRIBUTION_AMOUNT,
            months=12,
            due_day=10,
            active=True,
            min_cash_reserve=Decimal("500.00"),
            max_member_exposure=Decimal("3000.00"),
            max_global_exposure=Decimal("20000.00"),
            max_exposure_ratio=Decimal("0.70"),
            max_simultaneous_loans=1,
            max_installments=12,
            grace_days=0,
            min_on_time_ratio=Decimal("0.80"),
            max_overdue_installments=2,
            max_installment_income_ratio=Decimal("0.35"),
            max_quota_multiple=Decimal("10.00"),
            max_loan_amount=Decimal("3000.00"),
            max_loan_income_multiple=Decimal("2.00"),
        )
        db.add(group)
        db.flush()

    return group


def get_or_create_user(db, index, name, email, cpf, income):
    user = db.query(User).filter(User.email == email).first()

    if user is None:
        from app.core.security import hash_password

        user = User(
            name=name,
            email=email,
            cpf=cpf,
            password_hash=hash_password(TEST_PASSWORD),
            role="USER",
            is_active=True,
        )
        db.add(user)
        db.flush()

    member = db.query(Member).filter(Member.user_id == user.id).first()

    return user, member


def create_member_if_needed(db, user, group, income):
    member = db.query(Member).filter(Member.user_id == user.id).first()

    if member is None:
        member = Member(
            user_id=user.id,
            group_id=group.id,
            status="ACTIVE",
            declared_monthly_income=income,
        )
        db.add(member)
        db.flush()

    quota = db.query(Quota).filter(Quota.member_id == member.id).first()

    if quota is None:
        quota = Quota(
            member_id=member.id,
            units=Decimal("1.0000"),
            status="ACTIVE",
        )
        db.add(quota)
        db.flush()

    return member


def create_paid_contribution(db, member, competence, amount, key):
    existing = (
        db.query(Contribution)
        .filter(
            Contribution.member_id == member.id,
            Contribution.competence == competence,
        )
        .first()
    )

    if existing:
        return existing

    payment = Payment(
        provider="HOMOLOGACAO",
        provider_order_id=f"HOMO-ORDER-{key}",
        provider_payment_id=f"HOMO-PAY-{key}",
        idempotency_key=f"homo-payment-{key}",
        amount=amount,
        status="approved",
        raw_status="approved",
        reference_type="CONTRIBUTION",
    )
    db.add(payment)
    db.flush()

    contribution = Contribution(
        member_id=member.id,
        competence=competence,
        amount=amount,
        status="PAID",
        payment_id=payment.id,
        pix_idempotency_key=f"homo-pix-{key}",
    )
    db.add(contribution)
    db.flush()

    payment.reference_id = str(contribution.id)

    # Ledger coletivo: entrada real/fictícia da contribuição.
    post_entry(
        db,
        "CAIXINHA",
        "CREDIT",
        amount,
        "CONTRIBUTION_PAYMENT",
        str(payment.id),
    )

    # Patrimônio próprio do participante.
    add_member_financial_entry(
        db,
        member,
        entry_type="CONTRIBUTION",
        direction="CREDIT",
        amount=amount,
        reference_type="CONTRIBUTION",
        reference_id=str(contribution.id),
        description="Contribuição fictícia de homologação.",
    )

    return contribution


def create_pending_contribution(db, member, competence, amount, key):
    existing = (
        db.query(Contribution)
        .filter(
            Contribution.member_id == member.id,
            Contribution.competence == competence,
        )
        .first()
    )

    if existing:
        return existing

    contribution = Contribution(
        member_id=member.id,
        competence=competence,
        amount=amount,
        status="PENDING",
        pix_idempotency_key=f"homo-pix-{key}",
    )
    db.add(contribution)
    db.flush()

    return contribution


def create_homologation_loan(
    db,
    member,
    principal,
    installments,
    status,
    key,
    admin_id,
):
    existing = (
        db.query(Loan)
        .filter(
            Loan.member_id == member.id,
            Loan.principal == Decimal(principal),
            Loan.installments == installments,
            Loan.status.in_(["REQUESTED", "APPROVED", "ACTIVE"]),
        )
        .order_by(Loan.id.desc())
        .first()
    )

    if existing:
        return existing

    loan = Loan(
        member_id=member.id,
        principal=Decimal(principal),
        monthly_rate=Decimal("0.20"),
        installments=installments,
        status="REQUESTED",
    )

    db.add(loan)
    db.flush()

    rows, _, _ = calculate_linear_amortization(
        loan.principal,
        loan.monthly_rate,
        loan.installments,
    )

    base_date = date.today()

    for row in rows:
        db.add(
            LoanInstallment(
                loan_id=loan.id,
                number=row["number"],
                due_date=add_months(base_date, row["number"]),
                principal=row["principal"],
                interest=row["interest"],
                amount=row["amount"],
                paid_amount=Decimal("0.00"),
                penalty_amount=Decimal("0.00"),
                paid_penalty_amount=Decimal("0.00"),
                status="OPEN",
            )
        )

    loan.status = status
    loan.decided_by = admin_id

    from datetime import datetime, timezone
    loan.decided_at = datetime.now(timezone.utc)

    db.flush()

    return loan


def audit(db, user_id, action, entity_type, entity_id, details):
    db.add(
        AuditLog(
            actor_user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            details=details,
        )
    )


def seed():
    db = SessionLocal()

    try:
        group = get_or_create_group(db)

        users = [
            (
                1,
                "HOMO Teste Normal",
                "homo.normal@example.com",
                "90000000001",
                Decimal("5000.00"),
            ),
            (
                2,
                "HOMO Teste Capacidade",
                "homo.capacidade@example.com",
                "90000000002",
                Decimal("6000.00"),
            ),
            (
                3,
                "HOMO Teste Baixo Saldo",
                "homo.baixosaldo@example.com",
                "90000000003",
                Decimal("3000.00"),
            ),
            (
                4,
                "HOMO Teste Empréstimo",
                "homo.emprestimo@example.com",
                "90000000004",
                Decimal("5000.00"),
            ),
            (
                5,
                "HOMO Teste Especial",
                "homo.especial@example.com",
                "90000000005",
                Decimal("10000.00"),
            ),
        ]

        members = []

        for index, name, email, cpf, income in users:
            user, _ = get_or_create_user(
                db,
                index,
                name,
                email,
                cpf,
                income,
            )
            member = create_member_if_needed(
                db,
                user,
                group,
                income,
            )
            members.append((user, member))

        today = date.today()

        # Participante 1: R$600 próprios.
        user1, member1 = members[0]
        for i in range(4):
            create_paid_contribution(
                db,
                member1,
                date(today.year, today.month, 1) - timedelta(days=30 * i),
                CONTRIBUTION_AMOUNT,
                f"M1-{i}",
            )

        # Participante 2: R$1.200 próprios.
        user2, member2 = members[1]
        for i in range(8):
            create_paid_contribution(
                db,
                member2,
                date(today.year, today.month, 1) - timedelta(days=30 * i),
                CONTRIBUTION_AMOUNT,
                f"M2-{i}",
            )

        # Participante 3: apenas R$300 próprios.
        user3, member3 = members[2]
        for i in range(2):
            create_paid_contribution(
                db,
                member3,
                date(today.year, today.month, 1) - timedelta(days=30 * i),
                CONTRIBUTION_AMOUNT,
                f"M3-{i}",
            )

        # Participante 4: R$750 próprios.
        user4, member4 = members[3]
        for i in range(5):
            create_paid_contribution(
                db,
                member4,
                date(today.year, today.month, 1) - timedelta(days=30 * i),
                CONTRIBUTION_AMOUNT,
                f"M4-{i}",
            )

        # Participante 5: R$2.000 próprios.
        user5, member5 = members[4]
        for i in range(10):
            create_paid_contribution(
                db,
                member5,
                date(today.year, today.month, 1) - timedelta(days=30 * i),
                CONTRIBUTION_AMOUNT,
                f"M5-{i}",
            )

        # Uma contribuição pendente para validar o fluxo de cobrança.
        create_pending_contribution(
            db,
            member1,
            today.replace(day=10),
            CONTRIBUTION_AMOUNT,
            "M1-PENDING",
        )

        audit(
            db,
            user1.id,
            "HOMOLOGATION_SEED",
            "MEMBER",
            member1.id,
            "Massa fictícia de homologação criada.",
        )

        audit(
            db,
            user5.id,
            "FINANCIAL_APPROVAL_EXCEPTION",
            "MEMBER",
            member5.id,
            (
                "Cenário fictício para homologação de crédito especial. "
                "Não representa operação financeira real."
            ),
        )


        # ============================================================
        # CENÁRIO DE HOMOLOGAÇÃO — EMPRÉSTIMO ATIVO
        # ============================================================
        from app.services.loan_engine_v17 import release_loan as release_loan_engine

        loan4 = create_homologation_loan(
            db,
            member4,
            Decimal("500.00"),
            6,
            "APPROVED",
            "L4",
            user1.id,
        )

        # Liberação usa a própria regra de produção:
        # Ledger LOAN_DISBURSEMENT + compromisso do principal.
        release_loan_engine(
            db,
            loan4,
            user1.id,
        )

        # ============================================================
        # CENÁRIO DE HOMOLOGAÇÃO — CRÉDITO ESPECIAL
        # ============================================================
        loan5 = create_homologation_loan(
            db,
            member5,
            Decimal("2500.00"),
            6,
            "REQUESTED",
            "L5",
            user1.id,
        )

        from app.services.approval_engine_v048 import assert_loan_approval_allowed

        assert_loan_approval_allowed(
            db,
            loan5,
            user1.id,
            force_exception=True,
            admin_note=(
                "HOMOLOGAÇÃO FICTÍCIA: crédito especial acima "
                "do limite normal. Não representa operação real."
            ),
        )

        loan5.status = "APPROVED"

        db.commit()

        print("======================================")
        print("FRcaixinha - Seed de Homologação")
        print("======================================")
        print(f"Grupo: {group.name}")
        print("Participantes fictícios: 5")
        print("Contribuições pagas criadas.")
        print("Contribuição pendente criada.")
        print("Ledger coletivo criado via post_entry().")
        print("Saldo próprio criado via MemberFinancialEntry.")
        print("Auditoria de homologação criada.")
        print("Senha dos usuários: Homologacao123!")
        print("======================================")
        print("SEED CONCLUÍDO")
        print("======================================")

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


if __name__ == "__main__":
    seed()
