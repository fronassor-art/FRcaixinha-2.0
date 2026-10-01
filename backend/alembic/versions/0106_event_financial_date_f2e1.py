"""Add nullable financial dates to settlement and reversal events.

Revision ID: 0106_event_financial_date_f2e1
Revises: 0105_ledger_financial_date_f1
"""

from alembic import op
import sqlalchemy as sa


revision = "0106_event_financial_date_f2e1"
down_revision = "0105_ledger_financial_date_f1"
branch_labels = None
depends_on = None


SETTLEMENTS = "payment_settlements"
REVERSALS = "payment_reversals"
FINANCIAL_DATE = "financial_date"


def upgrade():
    op.add_column(SETTLEMENTS, sa.Column(FINANCIAL_DATE, sa.Date(), nullable=True))
    op.add_column(REVERSALS, sa.Column(FINANCIAL_DATE, sa.Date(), nullable=True))


def downgrade():
    bind = op.get_bind()
    for table in (SETTLEMENTS, REVERSALS):
        has_financial_date = bind.execute(
            sa.text(
                f"SELECT 1 FROM {table} "
                f"WHERE {FINANCIAL_DATE} IS NOT NULL LIMIT 1"
            )
        ).first()
        if has_financial_date:
            raise RuntimeError(
                f"Cannot downgrade event financial-date schema while "
                f"{table} contains financial-date values"
            )

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(REVERSALS) as batch_op:
            batch_op.drop_column(FINANCIAL_DATE)
        with op.batch_alter_table(SETTLEMENTS) as batch_op:
            batch_op.drop_column(FINANCIAL_DATE)
        return

    op.drop_column(REVERSALS, FINANCIAL_DATE)
    op.drop_column(SETTLEMENTS, FINANCIAL_DATE)
