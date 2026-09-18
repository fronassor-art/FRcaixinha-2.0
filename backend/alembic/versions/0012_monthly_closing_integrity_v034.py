"""v0.34: immutable monthly closing snapshot and installment paid timestamp."""
from alembic import op
import sqlalchemy as sa

revision = "0012_monthly_closing_integrity_v034"
down_revision = "0011_penalty_allocation_v029"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("loan_installments", sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("monthly_closings", sa.Column("snapshot_json", sa.Text(), nullable=True))
    op.add_column("monthly_closings", sa.Column("snapshot_hash", sa.String(64), nullable=True))
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("monthly_closings") as batch_op:
            batch_op.create_unique_constraint("uq_monthly_closing_snapshot_hash", ["snapshot_hash"])
    else:
        op.create_unique_constraint("uq_monthly_closing_snapshot_hash", "monthly_closings", ["snapshot_hash"])

def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("monthly_closings") as batch_op:
            batch_op.drop_constraint("uq_monthly_closing_snapshot_hash", type_="unique")
    else:
        op.drop_constraint("uq_monthly_closing_snapshot_hash", "monthly_closings", type_="unique")
    op.drop_column("monthly_closings", "snapshot_hash")
    op.drop_column("monthly_closings", "snapshot_json")
    op.drop_column("loan_installments", "paid_at")
