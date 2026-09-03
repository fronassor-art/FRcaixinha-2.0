from alembic import op
import sqlalchemy as sa
revision='0038_command_center_v061'
down_revision='0037_operational_control_v060'
branch_labels=None
depends_on=None
def upgrade():
    op.create_table('operational_action_records',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('snapshot_id',sa.Integer(),sa.ForeignKey('operational_control_snapshots.id'),nullable=True),
        sa.Column('action_code',sa.String(60),nullable=False),
        sa.Column('status',sa.String(20),nullable=False,server_default='OPEN'),
        sa.Column('assigned_to',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
        sa.Column('acknowledged_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
        sa.Column('acknowledged_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('note',sa.Text(),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_operational_action_records_snapshot_id','operational_action_records',['snapshot_id'])
    op.create_index('ix_operational_action_records_action_code','operational_action_records',['action_code'])
    op.create_index('ix_operational_action_records_status','operational_action_records',['status'])
    op.create_index('ix_operational_action_records_assigned_to','operational_action_records',['assigned_to'])
    op.create_index('ix_operational_action_records_created_at','operational_action_records',['created_at'])
def downgrade():
    for n in ['ix_operational_action_records_created_at','ix_operational_action_records_assigned_to','ix_operational_action_records_status','ix_operational_action_records_action_code','ix_operational_action_records_snapshot_id']:
        op.drop_index(n,table_name='operational_action_records')
    op.drop_table('operational_action_records')
