"""Add nullable ledger financial dates and hash version metadata.

Revision ID: 0105_ledger_financial_date_f1
Revises: 0104_scheduler_runs_g3d4a
"""

from alembic import op
import sqlalchemy as sa


revision = "0105_ledger_financial_date_f1"
down_revision = "0104_scheduler_runs_g3d4a"
branch_labels = None
depends_on = None


TABLE = "ledger_entries"
CHECK_NAME = "ck_ledger_entries_financial_date_hash_version"
INDEX_NAME = "ix_ledger_entries_financial_date"
CHECK_SQL = (
    "(financial_date IS NULL AND hash_version IS NULL) OR "
    "(financial_date IS NOT NULL AND hash_version IS NOT NULL AND hash_version = 2)"
)


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite needs batch recreation to add a table check constraint.
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.add_column(sa.Column("financial_date", sa.Date(), nullable=True))
            batch_op.add_column(sa.Column("hash_version", sa.SmallInteger(), nullable=True))
            batch_op.create_check_constraint(CHECK_NAME, CHECK_SQL)
            batch_op.create_index(INDEX_NAME, ["financial_date"], unique=False)
        return

    op.add_column(TABLE, sa.Column("financial_date", sa.Date(), nullable=True))
    op.add_column(TABLE, sa.Column("hash_version", sa.SmallInteger(), nullable=True))
    op.create_check_constraint(CHECK_NAME, TABLE, CHECK_SQL)
    op.create_index(INDEX_NAME, TABLE, ["financial_date"], unique=False)


def downgrade():
    bind = op.get_bind()
    has_v2 = bind.execute(
        sa.text(
            "SELECT 1 FROM ledger_entries "
            "WHERE financial_date IS NOT NULL OR hash_version IS NOT NULL LIMIT 1"
        )
    ).first()
    if has_v2:
        raise RuntimeError(
            "Cannot downgrade ledger financial-date schema while versioned ledger rows exist"
        )

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.drop_index(INDEX_NAME)
            batch_op.drop_constraint(CHECK_NAME, type_="check")
            batch_op.drop_column("hash_version")
            batch_op.drop_column("financial_date")
        return

    op.drop_index(INDEX_NAME, table_name=TABLE)
    op.drop_constraint(CHECK_NAME, TABLE, type_="check")
    op.drop_column(TABLE, "hash_version")
    op.drop_column(TABLE, "financial_date")
