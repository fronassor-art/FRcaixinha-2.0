"""add payment attempt schema foundation"""

from alembic import op
import sqlalchemy as sa


revision = "0093_payment_attempt_schema_foundation"
down_revision = "0092_versioned_late_charge_foundation"
branch_labels = None
depends_on = None


INDEX_NAME = "uq_payments_reference_pending"

_PAYMENT_ATTEMPT_COLUMNS = (
    "attempt_status",
    "calculated_for_date",
    "financial_snapshot_json",
    "snapshot_hash",
    "reconciliation_status",
)


def upgrade():
    op.add_column(
        "payments",
        sa.Column("attempt_status", sa.String(30), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("calculated_for_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("financial_snapshot_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("snapshot_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("reconciliation_status", sa.String(30), nullable=True),
    )
    op.create_index(
        INDEX_NAME,
        "payments",
        ["reference_type", "reference_id"],
        unique=True,
        sqlite_where=sa.text("attempt_status = 'PENDING'"),
        postgresql_where=sa.text("attempt_status = 'PENDING'"),
    )


def downgrade():
    op.drop_index(INDEX_NAME, table_name="payments")

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("payments", recreate="always") as batch_op:
            for column in reversed(_PAYMENT_ATTEMPT_COLUMNS):
                batch_op.drop_column(column)
    else:
        for column in reversed(_PAYMENT_ATTEMPT_COLUMNS):
            op.drop_column("payments", column)
