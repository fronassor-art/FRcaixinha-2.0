from alembic import op
import sqlalchemy as sa

revision = "0002_webhook_events"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("event_id", sa.String(150), nullable=False),
        sa.Column("event_type", sa.String(100)),
        sa.Column("processed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "event_id", name="uq_webhook_provider_event"),
    )

def downgrade():
    op.drop_table("webhook_events")
