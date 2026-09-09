"""v0.45 operational security and LGPD controls"""
from alembic import op
import sqlalchemy as sa

revision = '0022_security_lgpd_v045'
down_revision = '0021_communication_v044'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        'consent_records',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('consent_type', sa.String(40), nullable=False),
        sa.Column('version', sa.String(30), nullable=False),
        sa.Column('granted', sa.Boolean(), nullable=False),
        sa.Column('source', sa.String(30), nullable=False, server_default='APP'),
        sa.Column('ip_address', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_consent_records_user', 'consent_records', ['user_id'])
    op.create_index('ix_consent_records_type_created', 'consent_records', ['consent_type','created_at'])
    op.create_table(
        'data_access_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('actor_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('subject_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('action', sa.String(60), nullable=False),
        sa.Column('resource', sa.String(80), nullable=False),
        sa.Column('ip_address', sa.String(64), nullable=True),
        sa.Column('user_agent', sa.String(512), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_data_access_logs_subject_created', 'data_access_logs', ['subject_user_id','created_at'])
    op.create_index('ix_data_access_logs_actor_created', 'data_access_logs', ['actor_user_id','created_at'])
    op.create_table(
        'privacy_requests',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('request_type', sa.String(30), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='REQUESTED'),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('decision_note', sa.Text(), nullable=True),
        sa.Column('decided_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_privacy_requests_user_status', 'privacy_requests', ['user_id','status'])

def downgrade():
    op.drop_index('ix_privacy_requests_user_status', table_name='privacy_requests')
    op.drop_table('privacy_requests')
    op.drop_index('ix_data_access_logs_actor_created', table_name='data_access_logs')
    op.drop_index('ix_data_access_logs_subject_created', table_name='data_access_logs')
    op.drop_table('data_access_logs')
    op.drop_index('ix_consent_records_type_created', table_name='consent_records')
    op.drop_index('ix_consent_records_user', table_name='consent_records')
    op.drop_table('consent_records')
