"""v0.29: explicit penalty allocation for loan installment payments.

Revision ID: 0011_penalty_allocation_v029
Revises: 0010_security_reconciliation
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_penalty_allocation_v029"
down_revision = "0010_security_reconciliation"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "loan_installments",
        sa.Column("paid_penalty_amount", sa.Numeric(14, 2), nullable=False, server_default="0.00"),
    )


def downgrade():
    op.drop_column("loan_installments", "paid_penalty_amount")
