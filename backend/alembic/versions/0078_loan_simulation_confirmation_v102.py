"""add auditable loan simulation receipts"""

from alembic import op
import sqlalchemy as sa


revision = "0078_loan_simulation_confirmation_v102"
down_revision = "0057_loan_installment_cap_v101"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "loan_simulations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("principal", sa.Numeric(14, 2), nullable=False),
        sa.Column("monthly_rate", sa.Numeric(8, 5), nullable=False),
        sa.Column("installments", sa.Integer(), nullable=False),
        sa.Column("calculation_version", sa.String(60), nullable=False),
        sa.Column("schedule_json", sa.Text(), nullable=False),
        sa.Column("schedule_hash", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("loan_id", sa.Integer(), sa.ForeignKey("loans.id"), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("installments >= 1 AND installments <= 6", name="ck_loan_simulations_installments_1_6"),
        sa.CheckConstraint("monthly_rate = 0.20", name="ck_loan_simulations_official_rate"),
    )
    op.create_index("ix_loan_simulations_member_id", "loan_simulations", ["member_id"])
    op.create_index("ix_loan_simulations_schedule_hash", "loan_simulations", ["schedule_hash"])
    op.create_index("ix_loan_simulations_token_hash", "loan_simulations", ["token_hash"], unique=True)
    op.create_index("ix_loan_simulations_status", "loan_simulations", ["status"])
    op.create_index("ix_loan_simulations_expires_at", "loan_simulations", ["expires_at"])


def downgrade():
    op.drop_index("ix_loan_simulations_expires_at", table_name="loan_simulations")
    op.drop_index("ix_loan_simulations_status", table_name="loan_simulations")
    op.drop_index("ix_loan_simulations_token_hash", table_name="loan_simulations")
    op.drop_index("ix_loan_simulations_schedule_hash", table_name="loan_simulations")
    op.drop_index("ix_loan_simulations_member_id", table_name="loan_simulations")
    op.drop_table("loan_simulations")
