from alembic import op
import sqlalchemy as sa
revision='0069'; down_revision='0068'; branch_labels=None; depends_on=None
def upgrade():
    op.create_table('continuous_improvement_executive_audit_snapshots',
      sa.Column('id',sa.Integer(),primary_key=True), sa.Column('status',sa.String(20),nullable=False),
      sa.Column('snapshot_json',sa.Text(),nullable=False), sa.Column('snapshot_hash',sa.String(64),nullable=False),
      sa.Column('generated_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
      sa.Column('created_at',sa.DateTime(timezone=True),nullable=False), sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_ci_exec_audit_status','continuous_improvement_executive_audit_snapshots',['status'])
    op.create_index('ix_ci_exec_audit_hash','continuous_improvement_executive_audit_snapshots',['snapshot_hash'],unique=True)
    op.create_index('ix_ci_exec_audit_created','continuous_improvement_executive_audit_snapshots',['created_at'])
def downgrade():
    for n in ['ix_ci_exec_audit_created','ix_ci_exec_audit_hash','ix_ci_exec_audit_status']: op.drop_index(n,table_name='continuous_improvement_executive_audit_snapshots')
    op.drop_table('continuous_improvement_executive_audit_snapshots')
