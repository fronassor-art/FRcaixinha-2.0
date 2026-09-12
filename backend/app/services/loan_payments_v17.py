from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import (
    LoanInstallment,
    Payment,
    LedgerEntry,
    Loan,
    Member,
    MemberFinancialEntry,
)
from app.services.loan_engine_v17 import apply_payment, ensure_loan_completion
from app.services.member_financial import add_member_financial_entry


def apply_confirmed_payment(db, payment: Payment, installment: LoanInstallment):
    if payment.ledger_posted_at is not None:
        return False

    amount = Decimal(payment.amount)

    # Guardamos os valores anteriores porque apply_payment()
    # atualiza paid_amount e paid_penalty_amount.
    paid_amount_before = Decimal(installment.paid_amount or 0)
    paid_penalty_before = Decimal(installment.paid_penalty_amount or 0)

    result = apply_payment(installment, amount)

    if result["applied"] <= 0:
        payment.ledger_posted_at = datetime.now(timezone.utc)
        return False

    ref = str(payment.id)

    penalty_applied = Decimal(result["penalty_applied"])
    base_applied = Decimal(result["base_applied"])

    # ============================================================
    # MULTA
    # ============================================================
    # Multas pertencem ao resultado coletivo da FRcaixinha.
    if penalty_applied > 0:
        exists = db.query(LedgerEntry).filter(
            LedgerEntry.reference_type == "LOAN_PENALTY_PAYMENT",
            LedgerEntry.reference_id == ref,
        ).first()

        if not exists:
            db.add(
                LedgerEntry(
                    account="CAIXINHA",
                    direction="CREDIT",
                    amount=penalty_applied,
                    reference_type="LOAN_PENALTY_PAYMENT",
                    reference_id=ref,
                )
            )

    # ============================================================
    # JUROS E PRINCIPAL
    # ============================================================
    # O pagamento da base é alocado primeiro para os juros
    # ainda não pagos e depois para o principal.
    interest_total = Decimal(installment.interest or 0)

    interest_paid_before = min(
        interest_total,
        paid_amount_before,
    )

    interest_open_before = max(
        Decimal("0.00"),
        interest_total - interest_paid_before,
    )

    interest_applied = min(
        base_applied,
        interest_open_before,
    )

    principal_applied = max(
        Decimal("0.00"),
        base_applied - interest_applied,
    )

    # ============================================================
    # JUROS -> CAIXINHA
    # ============================================================
    if interest_applied > 0:
        exists = db.query(LedgerEntry).filter(
            LedgerEntry.reference_type == "LOAN_INTEREST_PAYMENT",
            LedgerEntry.reference_id == ref,
        ).first()

        if not exists:
            db.add(
                LedgerEntry(
                    account="CAIXINHA",
                    direction="CREDIT",
                    amount=interest_applied,
                    reference_type="LOAN_INTEREST_PAYMENT",
                    reference_id=ref,
                )
            )

    # ============================================================
    # PRINCIPAL -> SALDO PRÓPRIO DO PARTICIPANTE
    # ============================================================
    if principal_applied > 0:
        loan = db.get(Loan, installment.loan_id)

        if loan is None:
            raise ValueError(
                "Empréstimo da parcela não encontrado."
            )

        member = db.get(Member, loan.member_id)

        if member is None:
            raise ValueError(
                "Participante do empréstimo não encontrado."
            )

        add_member_financial_entry(
            db=db,
            member=member,
            entry_type="LOAN_PRINCIPAL_PAYMENT",
            direction="CREDIT",
            amount=principal_applied,
            reference_type="LOAN_PRINCIPAL_PAYMENT",
            reference_id=ref,
            description="Pagamento de principal de parcela de empréstimo.",
        )

    # ============================================================
    # FINALIZAÇÃO
    # ============================================================
    payment.ledger_posted_at = datetime.now(timezone.utc)

    loan = db.get(Loan, installment.loan_id)

    if loan:
        ensure_loan_completion(db, loan)

    return True


def settle_loan_with_own_balance(db, loan: Loan, actor_id: int):
    """Liquida integralmente o principal de um empréstimo usando saldo próprio.

    Nesta primeira versão, a operação trata somente empréstimos sem
    encargos vencidos. Juros futuros são integralmente dispensados.
    """

    from app.models import AuditLog
    from app.services.member_financial import get_member_financial_position

    # ------------------------------------------------------------
    # PARTICIPANTE
    # ------------------------------------------------------------
    member = db.get(Member, loan.member_id)
    if member is None:
        raise ValueError("Participante do empréstimo não encontrado.")

    # ------------------------------------------------------------
    # IDEMPOTÊNCIA
    # ------------------------------------------------------------
    existing = (
        db.query(MemberFinancialEntry)
        .filter(
            MemberFinancialEntry.entry_type == "OWN_BALANCE_SETTLEMENT",
            MemberFinancialEntry.reference_type == "OWN_BALANCE_SETTLEMENT",
            MemberFinancialEntry.reference_id == str(loan.id),
        )
        .first()
    )

    if existing:
        return {
            "settled": True,
            "settlement_amount": Decimal(existing.amount),
            "idempotent": True,
        }

    # ------------------------------------------------------------
    # STATUS
    # ------------------------------------------------------------
    if loan.status not in {"ACTIVE", "OVERDUE", "IN_COLLECTION"}:
        raise ValueError(
            f"Empréstimo não pode ser liquidado no status {loan.status}."
        )

    # ------------------------------------------------------------
    # PARCELAS
    # ------------------------------------------------------------
    installments = (
        db.query(LoanInstallment)
        .filter(LoanInstallment.loan_id == loan.id)
        .order_by(LoanInstallment.number)
        .all()
    )

    if not installments:
        raise ValueError("Empréstimo não possui parcelas.")

    # Nesta primeira implementação não permitimos liquidação
    # simplificada quando existem multas em aberto.
    penalty_open = sum(
        max(
            Decimal("0.00"),
            Decimal(i.penalty_amount or 0)
            - Decimal(i.paid_penalty_amount or 0),
        )
        for i in installments
    )

    if penalty_open > 0:
        raise ValueError(
            "Existem encargos vencidos. Liquidação com saldo próprio "
            "deve tratar esses encargos separadamente."
        )

    # ------------------------------------------------------------
    # PRINCIPAL EM ABERTO
    # ------------------------------------------------------------
    principal_settled = Decimal(
        loan.principal_settled_with_own_balance or 0
    )

    principal_open = max(
        Decimal("0.00"),
        Decimal(loan.principal or 0) - principal_settled,
    ).quantize(Decimal("0.01"))

    if principal_open <= 0:
        raise ValueError("Não existe principal em aberto para liquidar.")

    # ------------------------------------------------------------
    # SALDO PRÓPRIO DISPONÍVEL
    # ------------------------------------------------------------
    position = get_member_financial_position(db, member)

    available_balance = Decimal(
        position["available_balance"]
    ).quantize(Decimal("0.01"))

    if available_balance < principal_open:
        raise ValueError(
            "Saldo próprio disponível insuficiente para liquidação."
        )

    # ------------------------------------------------------------
    # DÉBITO DO SALDO PRÓPRIO
    # ------------------------------------------------------------
    add_member_financial_entry(
        db=db,
        member=member,
        entry_type="OWN_BALANCE_SETTLEMENT",
        direction="DEBIT",
        amount=principal_open,
        reference_type="OWN_BALANCE_SETTLEMENT",
        reference_id=str(loan.id),
        description=(
            "Liquidação integral do principal do empréstimo "
            "com saldo próprio. Juros futuros dispensados."
        ),
    )

    # ------------------------------------------------------------
    # REGISTRO DO PRINCIPAL LIQUIDADO COM SALDO PRÓPRIO
    # ------------------------------------------------------------
    loan.principal_settled_with_own_balance = (
        principal_open
    ).quantize(Decimal("0.01"))

    # ------------------------------------------------------------
    # FECHAMENTO DAS PARCELAS
    # ------------------------------------------------------------
    for installment in installments:
        installment.paid_penalty_amount = Decimal(
            installment.penalty_amount or 0
        )
        installment.status = "PAID"
        installment.paid_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------
    # COMPROMISSO DO PRINCIPAL
    # ------------------------------------------------------------

    # ------------------------------------------------------------
    # EMPRÉSTIMO
    # ------------------------------------------------------------
    loan.status = "PAID"
    loan.paid_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------
    # AUDITORIA
    # ------------------------------------------------------------
    db.add(
        AuditLog(
            actor_user_id=actor_id,
            action="LOAN_SETTLEMENT_OWN_BALANCE",
            entity_type="LOAN",
            entity_id=str(loan.id),
            details=(
                f"Liquidação com saldo próprio. "
                f"Principal liquidado: {principal_open}. "
                f"Juros futuros dispensados."
            ),
        )
    )

    db.flush()

    return {
        "settled": True,
        "settlement_amount": principal_open,
        "idempotent": False,
    }

def renegotiate_loan_with_own_balance(
    db: Session,
    loan: Loan,
    amount: Decimal,
    actor_id: int,
):
    """Amortiza parcialmente o principal de um empréstimo com saldo próprio."""
    from app.models import AuditLog
    from app.services.member_financial import get_member_financial_position

    amount = Decimal(amount).quantize(Decimal("0.01"))

    if amount <= Decimal("0.00"):
        raise ValueError("O valor da renegociação deve ser maior que zero.")

    member = db.get(Member, loan.member_id)
    if member is None:
        raise ValueError("Participante do empréstimo não encontrado.")

    if loan.status not in {"ACTIVE", "OVERDUE", "IN_COLLECTION"}:
        raise ValueError(
            f"Empréstimo não pode ser renegociado no status {loan.status}."
        )

    existing = (
        db.query(MemberFinancialEntry)
        .filter(
            MemberFinancialEntry.entry_type == "OWN_BALANCE_RENEGOTIATION",
            MemberFinancialEntry.reference_type == "OWN_BALANCE_RENEGOTIATION",
            MemberFinancialEntry.reference_id == str(loan.id),
        )
        .first()
    )

    if existing:
        remaining = (
            Decimal(loan.principal)
            - Decimal(loan.principal_settled_with_own_balance or 0)
        ).quantize(Decimal("0.01"))

        return {
            "renegotiated": True,
            "amount_applied": Decimal(existing.amount),
            "remaining_principal": max(Decimal("0.00"), remaining),
            "idempotent": True,
        }

    principal_remaining = (
        Decimal(loan.principal)
        - Decimal(loan.principal_settled_with_own_balance or 0)
    ).quantize(Decimal("0.01"))

    if principal_remaining <= Decimal("0.00"):
        raise ValueError("Não existe principal em aberto para renegociar.")

    if amount > principal_remaining:
        raise ValueError(
            "O valor da renegociação não pode exceder o principal em aberto."
        )

    position = get_member_financial_position(db, member)

    available_balance = Decimal(
        position["available_balance"]
    ).quantize(Decimal("0.01"))

    if amount > available_balance:
        raise ValueError(
            "Saldo próprio disponível insuficiente para renegociação."
        )

    add_member_financial_entry(
        db=db,
        member=member,
        entry_type="OWN_BALANCE_RENEGOTIATION",
        direction="DEBIT",
        amount=amount,
        reference_type="OWN_BALANCE_RENEGOTIATION",
        reference_id=str(loan.id),
        description=(
            "Amortização parcial do principal com saldo próprio. "
            "Juros futuros permanecem sujeitos às regras do novo saldo devedor."
        ),
    )

    loan.principal_settled_with_own_balance = (
        Decimal(loan.principal_settled_with_own_balance or 0) + amount
    ).quantize(Decimal("0.01"))

    remaining_principal = (
        Decimal(loan.principal)
        - loan.principal_settled_with_own_balance
    ).quantize(Decimal("0.01"))

    db.add(
        AuditLog(
            actor_user_id=actor_id,
            action="LOAN_RENEGOTIATION_OWN_BALANCE",
            entity_type="LOAN",
            entity_id=str(loan.id),
            details=(
                f"Renegociação parcial com saldo próprio. "
                f"Valor aplicado: {amount}. "
                f"Principal original: {loan.principal}. "
                f"Principal liquidado com saldo próprio: "
                f"{loan.principal_settled_with_own_balance}. "
                f"Principal restante: {remaining_principal}."
            ),
        )
    )

    db.flush()

    return {
        "renegotiated": True,
        "amount_applied": amount,
        "remaining_principal": remaining_principal,
        "idempotent": False,
    }
