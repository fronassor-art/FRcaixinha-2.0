"""v0.46 advanced security, 2FA, devices and security events"""
from alembic import op
import sqlalchemy as sa
revision='0023_security_advanced_v046'; down_revision='0022_security_lgpd_v045'; branch_labels=None; depends_on=None

def upgrade():
    op.create_table('user_security', sa.Column('id',sa.Integer(),primary_key=True), sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False,unique=True), sa.Column('totp_secret',sa.Text()), sa.Column('totp_enabled',sa.Boolean(),nullable=False,server_default=sa.false()), sa.Column('recovery_codes',sa.Text()), sa.Column('created_at',sa.DateTime(timezone=True),nullable=False), sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_user_security_user','user_security',['user_id'])
    op.create_table('trusted_devices', sa.Column('id',sa.Integer(),primary_key=True), sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False), sa.Column('device_token_hash',sa.String(64),nullable=False,unique=True), sa.Column('label',sa.String(120)), sa.Column('ip_address',sa.String(64)), sa.Column('user_agent',sa.String(512)), sa.Column('created_at',sa.DateTime(timezone=True),nullable=False), sa.Column('last_seen_at',sa.DateTime(timezone=True),nullable=False), sa.Column('revoked_at',sa.DateTime(timezone=True)))
    op.create_index('ix_trusted_devices_user','trusted_devices',['user_id'])
    op.create_table('security_events', sa.Column('id',sa.Integer(),primary_key=True), sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id')), sa.Column('event_type',sa.String(60),nullable=False), sa.Column('severity',sa.String(20),nullable=False,server_default='INFO'), sa.Column('ip_address',sa.String(64)), sa.Column('user_agent',sa.String(512)), sa.Column('details',sa.Text()), sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_security_events_user','security_events',['user_id']); op.create_index('ix_security_events_type','security_events',['event_type']); op.create_index('ix_security_events_created','security_events',['created_at'])

def downgrade():
    op.drop_index('ix_security_events_created',table_name='security_events'); op.drop_index('ix_security_events_type',table_name='security_events'); op.drop_index('ix_security_events_user',table_name='security_events'); op.drop_table('security_events'); op.drop_index('ix_trusted_devices_user',table_name='trusted_devices'); op.drop_table('trusted_devices'); op.drop_index('ix_user_security_user',table_name='user_security'); op.drop_table('user_security')
