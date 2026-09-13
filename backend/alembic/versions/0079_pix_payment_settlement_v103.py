"""add auditable PIX payment settlements"""

from alembic import op
import sqlalchemy as sa


revision = "0079_pix_payment_settlement_v103"
down_revision = "0078_loan_simulation_confirmation_v102"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("payments", sa.Column("external_reference", sa.String(150), nullable=True))
    op.add_column("payments", sa.Column("pix_txid", sa.String(100), nullable=True))
    op.add_column("payments", sa.Column("end_to_end_id", sa.String(100), nullable=True))
    op.add_column("payments", sa.Column("provider_status_detail", sa.String(150), nullable=True))
    op.add_column("payments", sa.Column("provider_payload_json", sa.Text(), nullable=True))
    op.add_column("payments", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payments", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payments", sa.Column("amount_received", sa.Numeric(14, 2), nullable=True))
    op.create_index("ix_payments_external_reference", "payments", ["external_reference"])
    op.create_index("ix_payments_confirmed_at", "payments", ["confirmed_at"])
    op.create_index("ix_payments_expires_at", "payments", ["expires_at"])
    op.create_index("ix_payments_status_expires_at", "payments", ["status", "expires_at"])
    op.create_index("uq_payments_provider_pix_txid", "payments", ["provider", "pix_txid"], unique=True)
    op.create_index("uq_payments_provider_end_to_end_id", "payments", ["provider", "end_to_end_id"], unique=True)

    op.add_column("contributions", sa.Column("due_date", sa.Date(), nullable=True))
    op.add_column("contributions", sa.Column("paid_amount", sa.Numeric(14, 2), nullable=True))
    op.add_column("contributions", sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_contributions_status_due_date", "contributions", ["status", "due_date"])
    op.create_index("ix_contributions_member_status_due_date", "contributions", ["member_id", "status", "due_date"])
    op.create_index("ix_loan_installments_status_due_date", "loan_installments", ["status", "due_date"])

    op.create_table(
        "payment_settlements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id"), nullable=False, unique=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("obligation_type", sa.String(30), nullable=False),
        sa.Column("contribution_id", sa.Integer(), sa.ForeignKey("contributions.id"), nullable=True),
        sa.Column("loan_installment_id", sa.Integer(), sa.ForeignKey("loan_installments.id"), nullable=True),
        sa.Column("amount_received", sa.Numeric(14, 2), nullable=False),
        sa.Column("amount_applied", sa.Numeric(14, 2), nullable=False),
        sa.Column("principal_applied", sa.Numeric(14, 2), nullable=False),
        sa.Column("interest_applied", sa.Numeric(14, 2), nullable=False),
        sa.Column("penalty_applied", sa.Numeric(14, 2), nullable=False),
        sa.Column("excess_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("obligation_status_before", sa.String(20), nullable=False),
        sa.Column("obligation_status_after", sa.String(20), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmation_source", sa.String(30), nullable=False),
        sa.Column("webhook_event_id", sa.Integer(), sa.ForeignKey("webhook_events.id"), nullable=True),
        sa.Column("receipt_number", sa.String(80), nullable=False, unique=True),
        sa.Column("receipt_version", sa.String(20), nullable=False),
        sa.Column("receipt_snapshot_json", sa.Text(), nullable=False),
        sa.Column("receipt_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_received >= 0 AND amount_applied >= 0 AND principal_applied >= 0 AND interest_applied >= 0 AND penalty_applied >= 0 AND excess_amount >= 0", name="ck_payment_settlements_nonnegative_amounts"),
        sa.CheckConstraint("amount_received = amount_applied + excess_amount", name="ck_payment_settlements_received_allocation"),
        sa.CheckConstraint("amount_applied = principal_applied + interest_applied + penalty_applied", name="ck_payment_settlements_applied_components"),
        sa.CheckConstraint("(obligation_type = 'CONTRIBUTION' AND contribution_id IS NOT NULL AND loan_installment_id IS NULL) OR (obligation_type = 'LOAN_INSTALLMENT' AND loan_installment_id IS NOT NULL AND contribution_id IS NULL)", name="ck_payment_settlements_single_obligation"),
        sa.CheckConstraint("obligation_status_before IN ('PENDING', 'PARTIAL', 'OVERDUE', 'PAID')", name="ck_payment_settlements_status_before"),
        sa.CheckConstraint("obligation_status_after IN ('PENDING', 'PARTIAL', 'OVERDUE', 'PAID')", name="ck_payment_settlements_status_after"),
        sa.CheckConstraint("receipt_version = 'v1'", name="ck_payment_settlements_receipt_version"),
    )
    op.create_index("ix_payment_settlements_member_confirmed", "payment_settlements", ["member_id", "confirmed_at"])
    op.create_index("ix_payment_settlements_contribution_id", "payment_settlements", ["contribution_id"])
    op.create_index("ix_payment_settlements_loan_installment_id", "payment_settlements", ["loan_installment_id"])
    op.create_index("ix_payment_settlements_webhook_event_id", "payment_settlements", ["webhook_event_id"])


def downgrade():
    op.drop_index("ix_payment_settlements_webhook_event_id", table_name="payment_settlements")
    op.drop_index("ix_payment_settlements_loan_installment_id", table_name="payment_settlements")
    op.drop_index("ix_payment_settlements_contribution_id", table_name="payment_settlements")
    op.drop_index("ix_payment_settlements_member_confirmed", table_name="payment_settlements")
    op.drop_table("payment_settlements")
    op.drop_index("ix_loan_installments_status_due_date", table_name="loan_installments")
    op.drop_index("ix_contributions_member_status_due_date", table_name="contributions")
    op.drop_index("ix_contributions_status_due_date", table_name="contributions")
    op.drop_column("contributions", "paid_at")
    op.drop_column("contributions", "paid_amount")
    op.drop_column("contributions", "due_date")
    op.drop_index("uq_payments_provider_end_to_end_id", table_name="payments")
    op.drop_index("uq_payments_provider_pix_txid", table_name="payments")
    op.drop_index("ix_payments_status_expires_at", table_name="payments")
    op.drop_index("ix_payments_expires_at", table_name="payments")
    op.drop_index("ix_payments_confirmed_at", table_name="payments")
    op.drop_index("ix_payments_external_reference", table_name="payments")
    op.drop_column("payments", "amount_received")
    op.drop_column("payments", "expires_at")
    op.drop_column("payments", "confirmed_at")
    op.drop_column("payments", "provider_payload_json")
    op.drop_column("payments", "provider_status_detail")
    op.drop_column("payments", "end_to_end_id")
    op.drop_column("payments", "pix_txid")
    op.drop_column("payments", "external_reference")
"""add auditable PIX payment settlements"""

from alembic import op
import sqlalchemy as sa

revision = "0079_pix_payment_settlement_v103"
down_revision = "0078_loan_simulation_confirmation_v102"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("payments", sa.Column("external_reference", sa.String(150), nullable=True))
    op.add_column("payments", sa.Column("pix_txid", sa.String(100), nullable=True))
    op.add_column("payments", sa.Column("end_to_end_id", sa.String(100), nullable=True))
    op.add_column("payments", sa.Column("provider_status_detail", sa.String(150), nullable=True))
    op.add_column("payments", sa.Column("provider_payload_json", sa.Text(), nullable=True))
    op.add_column("payments", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payments", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payments", sa.Column("amount_received", sa.Numeric(14, 2), nullable=True))
    op.create_index("ix_payments_external_reference", "payments", ["external_reference"])
    op.create_index("ix_payments_confirmed_at", "payments", ["confirmed_at"])
    op.create_index("ix_payments_expires_at", "payments", ["expires_at"])
    op.create_index("ix_payments_status_expires_at", "payments", ["status", "expires_at"])
    op.create_index("uq_payments_provider_pix_txid", "payments", ["provider", "pix_txid"], unique=True)
    op.create_index("uq_payments_provider_end_to_end_id", "payments", ["provider", "end_to_end_id"], unique=True)

    op.add_column("contributions", sa.Column("due_date", sa.Date(), nullable=True))
    op.add_column("contributions", sa.Column("paid_amount", sa.Numeric(14, 2), nullable=True))
    op.add_column("contributions", sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_contributions_status_due_date", "contributions", ["status", "due_date"])
    op.create_index("ix_contributions_member_status_due_date", "contributions", ["member_id", "status", "due_date"])
    op.create_index("ix_loan_installments_status_due_date", "loan_installments", ["status", "due_date"])

    op.create_table(
        "payment_settlements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id"), nullable=False, unique=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("obligation_type", sa.String(30), nullable=False),
        sa.Column("contribution_id", sa.Integer(), sa.ForeignKey("contributions.id"), nullable=True),
        sa.Column("loan_installment_id", sa.Integer(), sa.ForeignKey("loan_installments.id"), nullable=True),
        sa.Column("amount_received", sa.Numeric(14, 2), nullable=False),
        sa.Column("amount_applied", sa.Numeric(14, 2), nullable=False),
        sa.Column("principal_applied", sa.Numeric(14, 2), nullable=False),
        sa.Column("interest_applied", sa.Numeric(14, 2), nullable=False),
        sa.Column("penalty_applied", sa.Numeric(14, 2), nullable=False),
        sa.Column("excess_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("obligation_status_before", sa.String(20), nullable=False),
        sa.Column("obligation_status_after", sa.String(20), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmation_source", sa.String(30), nullable=False),
        sa.Column("webhook_event_id", sa.Integer(), sa.ForeignKey("webhook_events.id"), nullable=True),
        sa.Column("receipt_number", sa.String(80), nullable=False, unique=True),
        sa.Column("receipt_version", sa.String(20), nullable=False),
        sa.Column("receipt_snapshot_json", sa.Text(), nullable=False),
        sa.Column("receipt_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_received >= 0 AND amount_applied >= 0 AND principal_applied >= 0 AND interest_applied >= 0 AND penalty_applied >= 0 AND excess_amount >= 0", name="ck_payment_settlements_nonnegative_amounts"),
        sa.CheckConstraint("amount_received = amount_applied + excess_amount", name="ck_payment_settlements_received_allocation"),
        sa.CheckConstraint("amount_applied = principal_applied + interest_applied + penalty_applied", name="ck_payment_settlements_applied_components"),
        sa.CheckConstraint("(obligation_type = 'CONTRIBUTION' AND contribution_id IS NOT NULL AND loan_installment_id IS NULL) OR (obligation_type = 'LOAN_INSTALLMENT' AND loan_installment_id IS NOT NULL AND contribution_id IS NULL)", name="ck_payment_settlements_single_obligation"),
        sa.CheckConstraint("obligation_status_before IN ('PENDING', 'PARTIAL', 'OVERDUE', 'PAID')", name="ck_payment_settlements_status_before"),
        sa.CheckConstraint("obligation_status_after IN ('PENDING', 'PARTIAL', 'OVERDUE', 'PAID')", name="ck_payment_settlements_status_after"),
        sa.CheckConstraint("receipt_version = 'v1'", name="ck_payment_settlements_receipt_version"),
    )
    op.create_index("ix_payment_settlements_member_confirmed", "payment_settlements", ["member_id", "confirmed_at"])
    op.create_index("ix_payment_settlements_contribution_id", "payment_settlements", ["contribution_id"])
    op.create_index("ix_payment_settlements_loan_installment_id", "payment_settlements", ["loan_installment_id"])
    op.create_index("ix_payment_settlements_webhook_event_id", "payment_settlements", ["webhook_event_id"])


def downgrade():
    op.drop_index("ix_payment_settlements_webhook_event_id", table_name="payment_settlements")
    op.drop_index("ix_payment_settlements_loan_installment_id", table_name="payment_settlements")
    op.drop_index("ix_payment_settlements_contribution_id", table_name="payment_settlements")
    op.drop_index("ix_payment_settlements_member_confirmed", table_name="payment_settlements")
    op.drop_table("payment_settlements")
    op.drop_index("ix_loan_installments_status_due_date", table_name="loan_installments")
    op.drop_index("ix_contributions_member_status_due_date", table_name="contributions")
    op.drop_index("ix_contributions_status_due_date", table_name="contributions")
    op.drop_column("contributions", "paid_at")
    op.drop_column("contributions", "paid_amount")
    op.drop_column("contributions", "due_date")
    op.drop_index("uq_payments_provider_end_to_end_id", table_name="payments")
    op.drop_index("uq_payments_provider_pix_txid", table_name="payments")
    op.drop_index("ix_payments_status_expires_at", table_name="payments")
    op.drop_index("ix_payments_expires_at", table_name="payments")
    op.drop_index("ix_payments_confirmed_at", table_name="payments")
    op.drop_index("ix_payments_external_reference", table_name="payments")
    op.drop_column("payments", "amount_received")
    op.drop_column("payments", "expires_at")
    op.drop_column("payments", "confirmed_at")
    op.drop_column("payments", "provider_payload_json")
    op.drop_column("payments", "provider_status_detail")
    op.drop_column("payments", "end_to_end_id")
    op.drop_column("payments", "pix_txid")
    op.drop_column("payments", "external_reference")
