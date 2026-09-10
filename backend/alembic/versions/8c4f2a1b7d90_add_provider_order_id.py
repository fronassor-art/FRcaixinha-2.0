from alembic import op
import sqlalchemy as sa

revision = "8c4f2a1b7d90"
down_revision = "3eb28ad54a96"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("payments", sa.Column("provider_order_id", sa.String(length=150), nullable=True))
    op.create_index("ix_payments_provider_order_id", "payments", ["provider_order_id"])

def downgrade():
    op.drop_index("ix_payments_provider_order_id", table_name="payments")
    op.drop_column("payments", "provider_order_id")
