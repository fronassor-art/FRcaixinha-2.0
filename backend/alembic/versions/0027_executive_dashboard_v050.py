from alembic import op
import sqlalchemy as sa
revision='0027_executive_dashboard_v050'; down_revision='0026_collection_recovery_v049'; branch_labels=None; depends_on=None

def upgrade():
    op.create_table('executive_dashboard_snapshots',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('snapshot_date',sa.Date(),nullable=False),
        sa.Column('status',sa.String(20),nullable=False,server_default='PASS'),
        sa.Column('snapshot_json',sa.Text(),nullable=False),
        sa.Column('snapshot_hash',sa.String(64),nullable=False),
        sa.Column('generated_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_executive_dashboard_snapshots_snapshot_date','executive_dashboard_snapshots',['snapshot_date'],unique=True)
    op.create_index('ix_executive_dashboard_snapshots_status','executive_dashboard_snapshots',['status'])
    op.create_index('ix_executive_dashboard_snapshots_snapshot_hash','executive_dashboard_snapshots',['snapshot_hash'],unique=True)

def downgrade():
    op.drop_index('ix_executive_dashboard_snapshots_snapshot_hash',table_name='executive_dashboard_snapshots')
    op.drop_index('ix_executive_dashboard_snapshots_status',table_name='executive_dashboard_snapshots')
    op.drop_index('ix_executive_dashboard_snapshots_snapshot_date',table_name='executive_dashboard_snapshots')
    op.drop_table('executive_dashboard_snapshots')
