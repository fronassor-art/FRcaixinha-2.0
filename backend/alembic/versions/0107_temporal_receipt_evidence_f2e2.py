"""Constrain temporal receipt versions to their persisted financial dates.

Revision ID: 0107_temporal_receipt_evidence_f2e2
Revises: 0106_event_financial_date_f2e1
"""

from alembic import op
import sqlalchemy as sa


revision = "0107_temporal_receipt_evidence_f2e2"
down_revision = "0106_event_financial_date_f2e1"
branch_labels = None
depends_on = None


SETTLEMENTS = "payment_settlements"
REVERSALS = "payment_reversals"

OLD_SETTLEMENT_CHECKS = {
    "ck_payment_settlements_receipt_version": "receipt_version IN ('v1', 'v2', 'v3', 'v4', 'v5')",
    "ck_payment_settlements_paid_at_version": "receipt_version IN ('v3', 'v4') OR (loan_paid_at_before IS NULL AND loan_paid_at_after IS NULL)",
    "ck_payment_settlements_installment_state_version": "receipt_version = 'v4' OR (loan_installment_status_before IS NULL AND loan_installment_status_after IS NULL AND loan_installment_paid_at_before IS NULL AND loan_installment_paid_at_after IS NULL)",
    "ck_payment_settlements_agreement_evidence_version": "receipt_version = 'v5' OR (agreement_installment_status_before IS NULL AND agreement_installment_status_after IS NULL AND agreement_installment_paid_at_before IS NULL AND agreement_installment_paid_at_after IS NULL AND agreement_installment_paid_amount_before IS NULL AND agreement_installment_paid_amount_after IS NULL AND agreement_installment_paid_penalty_amount_before IS NULL AND agreement_installment_paid_penalty_amount_after IS NULL AND collection_agreement_status_before IS NULL AND collection_agreement_status_after IS NULL AND collection_agreement_state_revision_before IS NULL AND collection_agreement_state_revision_after IS NULL)",
}

NEW_SETTLEMENT_CHECKS = {
    "ck_payment_settlements_receipt_version": "receipt_version IN ('v1', 'v2', 'v3', 'v4', 'v5', 'v6')",
    "ck_payment_settlements_paid_at_version": "receipt_version IN ('v3', 'v4') OR (receipt_version = 'v6' AND obligation_type = 'LOAN_INSTALLMENT') OR (loan_paid_at_before IS NULL AND loan_paid_at_after IS NULL)",
    "ck_payment_settlements_installment_state_version": "receipt_version = 'v4' OR (receipt_version = 'v6' AND obligation_type = 'LOAN_INSTALLMENT') OR (loan_installment_status_before IS NULL AND loan_installment_status_after IS NULL AND loan_installment_paid_at_before IS NULL AND loan_installment_paid_at_after IS NULL)",
    "ck_payment_settlements_agreement_evidence_version": "receipt_version IN ('v5', 'v6') AND (receipt_version != 'v6' OR obligation_type = 'AGREEMENT_INSTALLMENT') OR (agreement_installment_status_before IS NULL AND agreement_installment_status_after IS NULL AND agreement_installment_paid_at_before IS NULL AND agreement_installment_paid_at_after IS NULL AND agreement_installment_paid_amount_before IS NULL AND agreement_installment_paid_amount_after IS NULL AND agreement_installment_paid_penalty_amount_before IS NULL AND agreement_installment_paid_penalty_amount_after IS NULL AND collection_agreement_status_before IS NULL AND collection_agreement_status_after IS NULL AND collection_agreement_state_revision_before IS NULL AND collection_agreement_state_revision_after IS NULL)",
    "ck_payment_settlements_temporal_receipt_date": "(receipt_version = 'v6' AND financial_date IS NOT NULL) OR (receipt_version != 'v6' AND financial_date IS NULL)",
    "ck_payment_settlements_v6_obligation_evidence": "receipt_version != 'v6' OR (obligation_type = 'LOAN_INSTALLMENT' AND loan_status_before IS NOT NULL AND loan_status_after IS NOT NULL AND loan_state_revision_before IS NOT NULL AND loan_state_revision_after = loan_state_revision_before + 1 AND loan_installment_status_before IS NOT NULL AND loan_installment_status_after IS NOT NULL AND agreement_installment_status_before IS NULL AND collection_agreement_status_before IS NULL) OR (obligation_type = 'AGREEMENT_INSTALLMENT' AND agreement_installment_status_before IS NOT NULL AND agreement_installment_status_after IS NOT NULL AND agreement_installment_paid_amount_before IS NOT NULL AND agreement_installment_paid_amount_after = agreement_installment_paid_amount_before + principal_applied AND agreement_installment_paid_penalty_amount_before IS NOT NULL AND agreement_installment_paid_penalty_amount_after = agreement_installment_paid_penalty_amount_before + penalty_applied AND collection_agreement_status_before IS NOT NULL AND collection_agreement_status_after IS NOT NULL AND collection_agreement_state_revision_before IS NOT NULL AND collection_agreement_state_revision_after = collection_agreement_state_revision_before + 1 AND loan_status_before IS NULL AND loan_installment_status_before IS NULL) OR (obligation_type = 'CONTRIBUTION' AND loan_status_before IS NULL AND loan_installment_status_before IS NULL AND agreement_installment_status_before IS NULL AND collection_agreement_status_before IS NULL)",
    "ck_payment_settlements_v6_agreement_complete": "receipt_version != 'v6' OR obligation_type != 'AGREEMENT_INSTALLMENT' OR (agreement_installment_paid_amount_before >= 0 AND agreement_installment_paid_amount_after >= 0 AND agreement_installment_paid_penalty_amount_before >= 0 AND agreement_installment_paid_penalty_amount_after >= 0 AND agreement_installment_status_before IN ('OPEN', 'PARTIAL', 'PAID') AND agreement_installment_status_after IN ('OPEN', 'PARTIAL', 'PAID') AND collection_agreement_status_before IN ('APPROVED', 'SETTLED') AND collection_agreement_status_after IN ('APPROVED', 'SETTLED'))",
    "ck_payment_settlements_v6_loan_complete": "receipt_version != 'v6' OR obligation_type != 'LOAN_INSTALLMENT' OR (loan_paid_at_before IS NULL OR loan_status_before != 'PAID') AND (loan_status_after = 'PAID' AND loan_paid_at_after IS NOT NULL OR loan_status_after != 'PAID' AND loan_paid_at_after IS NULL)",
}

OLD_REVERSAL_CHECKS = {
    "ck_payment_reversals_receipt_version": "receipt_version = 'v1'",
}
NEW_REVERSAL_CHECKS = {
    "ck_payment_reversals_receipt_version": "receipt_version IN ('v1', 'v2')",
    "ck_payment_reversals_temporal_receipt_date": "(receipt_version = 'v1' AND financial_date IS NULL) OR (receipt_version = 'v2' AND financial_date IS NOT NULL)",
}


def _replace_checks(table, old, new):
    recreate = "always" if op.get_context().dialect.name == "sqlite" else "auto"
    with op.batch_alter_table(table, recreate=recreate) as batch:
        for name in old:
            batch.drop_constraint(name, type_="check")
        for name, expression in new.items():
            batch.create_check_constraint(name, expression)


def upgrade():
    # No data rewrite or backfill: all existing receipts stay legacy/NULL.
    _replace_checks(SETTLEMENTS, OLD_SETTLEMENT_CHECKS, NEW_SETTLEMENT_CHECKS)
    _replace_checks(REVERSALS, OLD_REVERSAL_CHECKS, NEW_REVERSAL_CHECKS)


def downgrade():
    bind = op.get_bind()
    for table, predicate in (
        (SETTLEMENTS, "receipt_version = 'v6' OR financial_date IS NOT NULL"),
        (REVERSALS, "receipt_version = 'v2' OR financial_date IS NOT NULL"),
    ):
        found = bind.execute(sa.text(f"SELECT 1 FROM {table} WHERE {predicate} LIMIT 1")).first()
        if found:
            raise RuntimeError(
                f"Cannot downgrade temporal receipt constraints while {table} contains temporal evidence"
            )
    _replace_checks(REVERSALS, NEW_REVERSAL_CHECKS, OLD_REVERSAL_CHECKS)
    _replace_checks(SETTLEMENTS, NEW_SETTLEMENT_CHECKS, OLD_SETTLEMENT_CHECKS)
