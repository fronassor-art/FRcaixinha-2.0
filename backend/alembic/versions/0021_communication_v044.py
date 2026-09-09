"""v0.44 notification center and communication preferences"""
from alembic import op
import sqlalchemy as sa

revision = '0021_communication_v044'
down_revision = '0020_reporting_v042'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        'notification_preferences',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('in_app_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('email_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('payment_alerts', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('loan_alerts', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('collection_alerts', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('account_alerts', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', name='uq_notification_preferences_user'),
    )
    op.create_index('ix_notification_preferences_user', 'notification_preferences', ['user_id'], unique=True)
    op.create_table(
        'notification_deliveries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('notification_id', sa.Integer(), sa.ForeignKey('notifications.id'), nullable=False),
        sa.Column('channel', sa.String(20), nullable=False),
        sa.Column('attempt', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('status', sa.String(20), nullable=False, server_default='PENDING'),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_notification_deliveries_notification', 'notification_deliveries', ['notification_id'])
    op.create_index('ix_notification_deliveries_status', 'notification_deliveries', ['status'])

def downgrade():
    op.drop_index('ix_notification_deliveries_status', table_name='notification_deliveries')
    op.drop_index('ix_notification_deliveries_notification', table_name='notification_deliveries')
    op.drop_table('notification_deliveries')
    op.drop_index('ix_notification_preferences_user', table_name='notification_preferences')
    op.drop_table('notification_preferences')
