"""financial operations and monthly closing
Revision ID: 0005_financial_operations
Revises: 0004_admin_indexes
"""
from alembic import op
import sqlalchemy as sa
revision="0005_financial_operations"; down_revision="0004_admin_indexes"; branch_labels=None; depends_on=None

def upgrade():
    op.create_table("expenses",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("description", sa.String(200), nullable=False),
        sa.Column("amount", sa.Numeric(14,2), nullable=False), sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("category", sa.String(80), nullable=False, server_default="GENERAL"), sa.Column("status", sa.String(20), nullable=False, server_default="POSTED"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id")), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_expenses_date","expenses",["expense_date"]); op.create_index("ix_expenses_category","expenses",["category"])
    op.create_table("monthly_closings",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("competence", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"), sa.Column("total_contributions", sa.Numeric(14,2), nullable=False, server_default="0"),
        sa.Column("total_expenses", sa.Numeric(14,2), nullable=False, server_default="0"), sa.Column("total_interest_received", sa.Numeric(14,2), nullable=False, server_default="0"),
        sa.Column("ledger_balance", sa.Numeric(14,2), nullable=False, server_default="0"), sa.Column("closed_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("closed_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("competence", name="uq_monthly_closing_competence"))
    op.create_index("ix_monthly_closings_competence","monthly_closings",["competence"])

def downgrade():
    op.drop_index("ix_monthly_closings_competence",table_name="monthly_closings"); op.drop_table("monthly_closings")
    op.drop_index("ix_expenses_category",table_name="expenses"); op.drop_index("ix_expenses_date",table_name="expenses"); op.drop_table("expenses")
