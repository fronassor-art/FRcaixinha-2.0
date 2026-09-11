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
    """Obtém ou cria a conta financeira própria do participante."""
    account = (
        db.query(MemberFinancialAccount)
        .filter(MemberFinancialAccount.member_id == member.id)
        .first()
    )

    if account is None:
        account = MemberFinancialAccount(member_id=member.id)
        db.add(account)
        db.flush()

    return account


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

    account = get_or_create_member_financial_account(db, member)

    entry = MemberFinancialEntry(
        account_id=account.id,
        entry_type=entry_type,
        direction=direction,
        amount=amount,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
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
            }:
                own_balance = max(
                    ZERO,
                    own_balance - amount,
                )
                committed_balance = max(
                    ZERO,
                    committed_balance - amount,
                )

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
