from datetime import datetime, timezone
from decimal import Decimal

from app.models import (
    LoanInstallment,
    Payment,
    LedgerEntry,
    Loan,
    Member,
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
