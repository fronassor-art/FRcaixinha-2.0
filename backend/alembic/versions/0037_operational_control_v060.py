from alembic import op
import sqlalchemy as sa
revision='0037_operational_control_v060'
down_revision='0036_secure_release_v059'
branch_labels=None
depends_on=None
def upgrade():
    op.create_table('operational_control_snapshots',
        sa.Column('id',sa.Integer(),primary_key=True),sa.Column('snapshot_date',sa.Date(),nullable=False),
        sa.Column('status',sa.String(20),nullable=False),sa.Column('action_count',sa.Integer(),nullable=False),
        sa.Column('snapshot_json',sa.Text(),nullable=False),sa.Column('snapshot_hash',sa.String(64),nullable=False),
        sa.Column('generated_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_operational_control_snapshots_snapshot_date','operational_control_snapshots',['snapshot_date'],unique=True)
    op.create_index('ix_operational_control_snapshots_status','operational_control_snapshots',['status'])
    op.create_index('ix_operational_control_snapshots_snapshot_hash','operational_control_snapshots',['snapshot_hash'],unique=True)
    op.create_index('ix_operational_control_snapshots_created_at','operational_control_snapshots',['created_at'])
def downgrade():
    for n in ['ix_operational_control_snapshots_created_at','ix_operational_control_snapshots_snapshot_hash','ix_operational_control_snapshots_status','ix_operational_control_snapshots_snapshot_date']:
        op.drop_index(n,table_name='operational_control_snapshots')
    op.drop_table('operational_control_snapshots')
