from alembic import op
import sqlalchemy as sa
revision='0067_continuous_improvement_certification_v090'; down_revision='0066_continuous_improvement_evidence_v089'; branch_labels=None; depends_on=None

def upgrade():
    op.create_table('continuous_improvement_certifications',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('execution_id',sa.Integer(),sa.ForeignKey('continuous_improvement_executions.id'),nullable=False),
        sa.Column('certificate_id',sa.String(80),nullable=False),
        sa.Column('status',sa.String(20),nullable=False),
        sa.Column('package_json',sa.Text(),nullable=False),
        sa.Column('package_hash',sa.String(64),nullable=False),
        sa.Column('certified_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('certified_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('certification_note',sa.Text(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_ci_cert_execution','continuous_improvement_certifications',['execution_id'],unique=True)
    op.create_index('ix_ci_cert_certificate_id','continuous_improvement_certifications',['certificate_id'],unique=True)
    op.create_index('ix_ci_cert_status','continuous_improvement_certifications',['status'])
    op.create_index('ix_ci_cert_package_hash','continuous_improvement_certifications',['package_hash'],unique=True)
    op.create_index('ix_ci_cert_certified_by','continuous_improvement_certifications',['certified_by'])
    op.create_index('ix_ci_cert_certified_at','continuous_improvement_certifications',['certified_at'])
    op.create_index('ix_ci_cert_created','continuous_improvement_certifications',['created_at'])

def downgrade():
    for n in ['ix_ci_cert_created','ix_ci_cert_certified_at','ix_ci_cert_certified_by','ix_ci_cert_package_hash','ix_ci_cert_status','ix_ci_cert_certificate_id','ix_ci_cert_execution']:
        op.drop_index(n,table_name='continuous_improvement_certifications')
    op.drop_table('continuous_improvement_certifications')
