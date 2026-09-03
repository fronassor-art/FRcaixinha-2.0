from alembic import op
import sqlalchemy as sa
revision='0068'; down_revision='0067_continuous_improvement_certification_v090'; branch_labels=None; depends_on=None
def upgrade():
    op.create_table('continuous_improvement_audit_snapshots',
      sa.Column('id',sa.Integer(),primary_key=True),
      sa.Column('execution_id',sa.Integer(),sa.ForeignKey('continuous_improvement_executions.id'),nullable=False),
      sa.Column('status',sa.String(20),nullable=False),sa.Column('snapshot_json',sa.Text(),nullable=False),
      sa.Column('snapshot_hash',sa.String(64),nullable=False),sa.Column('generated_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
      sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_ci_audit_execution','continuous_improvement_audit_snapshots',['execution_id'])
    op.create_index('ix_ci_audit_status','continuous_improvement_audit_snapshots',['status'])
    op.create_index('ix_ci_audit_hash','continuous_improvement_audit_snapshots',['snapshot_hash'],unique=True)
    op.create_index('ix_ci_audit_created','continuous_improvement_audit_snapshots',['created_at'])
def downgrade():
    for n in ['ix_ci_audit_created','ix_ci_audit_hash','ix_ci_audit_status','ix_ci_audit_execution']: op.drop_index(n,table_name='continuous_improvement_audit_snapshots')
    op.drop_table('continuous_improvement_audit_snapshots')
