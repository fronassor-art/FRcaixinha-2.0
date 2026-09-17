"""Restore the MonthlyClosing snapshot columns in the migrated schema."""

from alembic import op
import sqlalchemy as sa


revision = "0088_monthly_closing_snapshot_schema_h3c_b2"
down_revision = "0087_monthly_closing_immutability_h3c_a1"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("monthly_closings") as batch_op:
            batch_op.add_column(sa.Column("snapshot_json", sa.Text(), nullable=True))
            batch_op.add_column(sa.Column("snapshot_hash", sa.String(length=64), nullable=True))
            batch_op.create_unique_constraint(
                "uq_monthly_closing_snapshot_hash", ["snapshot_hash"]
            )
    else:
        op.add_column("monthly_closings", sa.Column("snapshot_json", sa.Text(), nullable=True))
        op.add_column(
            "monthly_closings", sa.Column("snapshot_hash", sa.String(length=64), nullable=True)
        )
        op.create_unique_constraint(
            "uq_monthly_closing_snapshot_hash", "monthly_closings", ["snapshot_hash"]
        )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("monthly_closings") as batch_op:
            batch_op.drop_constraint("uq_monthly_closing_snapshot_hash", type_="unique")
            batch_op.drop_column("snapshot_hash")
            batch_op.drop_column("snapshot_json")
    else:
        op.drop_constraint(
            "uq_monthly_closing_snapshot_hash", "monthly_closings", type_="unique"
        )
        op.drop_column("monthly_closings", "snapshot_hash")
        op.drop_column("monthly_closings", "snapshot_json")
