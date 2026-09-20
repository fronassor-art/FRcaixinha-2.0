from decimal import Decimal
from sqlalchemy.orm import Session

from app.models import (
    Member,
    MemberFinancialAccount,
    MemberFinancialEntry,
)

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def get_or_create_member_financial_account(
    db: Session,
    member: Member,
) -> MemberFinancialAccount:
    """Obtém/cria a conta sob o mutex financeiro do membro."""
    _, account = lock_member_financial_account(db, member)
    return account


def lock_member_financial_account(
    db: Session,
    member_or_id: Member | int,
) -> tuple[Member, MemberFinancialAccount]:
    """Adquire ``Member -> Account``; não faz commit.

    PostgreSQL usa locks de linha. SQLite mantém apenas o caminho lógico;
    não oferece semântica equivalente de row-level lock.
    """
    member_id = member_or_id.id if isinstance(member_or_id, Member) else member_or_id
    with db.no_autoflush:
        member_query = db.query(Member).filter(Member.id == member_id)
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            member_query = member_query.with_for_update().populate_existing()
        locked_member = member_query.one_or_none()
        if locked_member is None:
            raise ValueError("Participante não encontrado.")

        account = (
            db.query(MemberFinancialAccount)
            .filter(MemberFinancialAccount.member_id == locked_member.id)
            .first()
        )
        if account is None:
            account = MemberFinancialAccount(member_id=locked_member.id)
            db.add(account)
            db.flush()

        account_query = db.query(MemberFinancialAccount).filter(
            MemberFinancialAccount.id == account.id
        )
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            account_query = account_query.with_for_update().populate_existing()
        locked_account = account_query.one()

    return locked_member, locked_account


def add_member_financial_entry(
    db: Session,
    member: Member,
    *,
    entry_type: str,
    direction: str,
    amount: Decimal,
    reference_type: str | None = None,
    reference_id: str | None = None,
    description: str | None = None,
    account: MemberFinancialAccount | None = None,
    contribution_id: int | None = None,
    payment_settlement_id: int | None = None,
) -> MemberFinancialEntry:
    """
    Registra um lançamento financeiro próprio do participante.

    A função não faz commit. O commit fica sob responsabilidade
    da transação que estiver executando a operação de negócio.
    """
    amount = Decimal(amount).quantize(CENT)

    if amount <= ZERO:
        raise ValueError("O valor do lançamento deve ser maior que zero.")

    if direction not in {"CREDIT", "DEBIT"}:
        raise ValueError("direction deve ser CREDIT ou DEBIT.")

    # Callers that already hold Loan must pass the Account returned by
    # lock_member_financial_account; otherwise this function acquires it.
    if account is None:
        member, account = lock_member_financial_account(db, member)

    entry = MemberFinancialEntry(
        account_id=account.id,
        entry_type=entry_type,
        direction=direction,
        amount=amount,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
        contribution_id=contribution_id,
        payment_settlement_id=payment_settlement_id,
    )

    db.add(entry)
    db.flush()

    return entry


def calculate_member_financial_position(entries):
    """
    Calcula a posição financeira própria do participante.

    Regras:
    - CONTRIBUTION aumenta o patrimônio próprio.
    - LOAN_PRINCIPAL_PAYMENT aumenta o patrimônio próprio.
    - LOAN_INTEREST_PAYMENT não aumenta o patrimônio próprio.
    - LOAN_PENALTY_PAYMENT não aumenta o patrimônio próprio.
    - LOAN_PRINCIPAL_COMMITMENT apenas aumenta o valor comprometido.
    - OWN_BALANCE_RENEGOTIATION reduz o patrimônio e o comprometido.
    - OWN_BALANCE_SETTLEMENT reduz o patrimônio e o comprometido.
    - LOAN_PRINCIPAL_REVERSAL reduz o patrimônio e recompõe o comprometido.
    - Saldo disponível = patrimônio próprio - comprometido.
    """
    own_balance = ZERO
    committed_balance = ZERO

    for entry in entries:
        amount = Decimal(entry.amount)

        if entry.direction == "CREDIT":
            if entry.entry_type in {
                "CONTRIBUTION",
                "LOAN_PRINCIPAL_PAYMENT",
                "ADJUSTMENT",
            }:
                own_balance += amount

            if entry.entry_type == "LOAN_PRINCIPAL_PAYMENT":
                committed_balance = max(
                    ZERO,
                    committed_balance - amount,
                )

            if entry.entry_type == "LOAN_PRINCIPAL_COMMITMENT":
                committed_balance += amount

        elif entry.direction == "DEBIT":
            if entry.entry_type in {
                "OWN_BALANCE_RENEGOTIATION",
                "OWN_BALANCE_SETTLEMENT",
                "CONTRIBUTION_REVERSAL",
            }:
                own_balance = max(
                    ZERO,
                    own_balance - amount,
                )
                committed_balance = max(
                    ZERO,
                    committed_balance - amount,
                )

            if entry.entry_type == "LOAN_PRINCIPAL_REVERSAL":
                own_balance -= amount
                committed_balance += amount

    available_balance = max(
        ZERO,
        own_balance - committed_balance,
    )

    return {
        "own_balance": own_balance.quantize(CENT),
        "committed_balance": committed_balance.quantize(CENT),
        "available_balance": available_balance.quantize(CENT),
    }


def get_member_financial_position(
    db: Session,
    member: Member,
):
    """Calcula a posição financeira atual diretamente do banco."""
    account = (
        db.query(MemberFinancialAccount)
        .filter(MemberFinancialAccount.member_id == member.id)
        .first()
    )

    if account is None:
        return {
            "own_balance": ZERO,
            "committed_balance": ZERO,
            "available_balance": ZERO,
        }

    entries = (
        db.query(MemberFinancialEntry)
        .filter(MemberFinancialEntry.account_id == account.id)
        .order_by(MemberFinancialEntry.id)
        .all()
    )

    return calculate_member_financial_position(entries)
