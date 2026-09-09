from alembic import op
import sqlalchemy as sa

revision = "0003_payment_ledger"
down_revision = "0002_webhook_events"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("payments", sa.Column("ledger_posted_at", sa.DateTime(timezone=True), nullable=True))

def downgrade():
    op.drop_column("payments", "ledger_posted_at")
