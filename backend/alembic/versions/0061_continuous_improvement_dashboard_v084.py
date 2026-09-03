from alembic import op
import sqlalchemy as sa
revision='0061_continuous_improvement_dashboard_v084'; down_revision='0060_continuous_improvement_tracking_v083'; branch_labels=None; depends_on=None
def upgrade():
    op.create_table('continuous_improvement_dashboard_snapshots',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('snapshot_date',sa.Date(),nullable=False),
        sa.Column('status',sa.String(20),nullable=False),
        sa.Column('snapshot_json',sa.Text(),nullable=False),
        sa.Column('snapshot_hash',sa.String(64),nullable=False),
        sa.Column('generated_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_ci_dashboard_date','continuous_improvement_dashboard_snapshots',['snapshot_date'],unique=True)
    op.create_index('ix_ci_dashboard_status','continuous_improvement_dashboard_snapshots',['status'])
    op.create_index('ix_ci_dashboard_hash','continuous_improvement_dashboard_snapshots',['snapshot_hash'],unique=True)
def downgrade():
    for n in ['ix_ci_dashboard_hash','ix_ci_dashboard_status','ix_ci_dashboard_date']: op.drop_index(n,table_name='continuous_improvement_dashboard_snapshots')
    op.drop_table('continuous_improvement_dashboard_snapshots')
