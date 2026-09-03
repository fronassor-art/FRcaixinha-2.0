from alembic import op
import sqlalchemy as sa
revision='0074'; down_revision='0073'; branch_labels=None; depends_on=None
def upgrade():
    op.create_table('continuous_improvement_compliance_snapshots', sa.Column('id',sa.Integer(),primary_key=True), sa.Column('snapshot_date',sa.Date(),nullable=False,unique=True), sa.Column('status',sa.String(20),nullable=False), sa.Column('snapshot_json',sa.Text(),nullable=False), sa.Column('snapshot_hash',sa.String(64),nullable=False,unique=True), sa.Column('generated_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True), sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_0074_status','continuous_improvement_compliance_snapshots',['status'])
    op.create_index('ix_0074_date','continuous_improvement_compliance_snapshots',['snapshot_date'])
    op.create_index('ix_0074_hash','continuous_improvement_compliance_snapshots',['snapshot_hash'],unique=True)
def downgrade():
    op.drop_index('ix_0074_hash',table_name='continuous_improvement_compliance_snapshots')
    op.drop_index('ix_0074_status',table_name='continuous_improvement_compliance_snapshots')
    op.drop_index('ix_0074_date',table_name='continuous_improvement_compliance_snapshots')
    op.drop_table('continuous_improvement_compliance_snapshots')
