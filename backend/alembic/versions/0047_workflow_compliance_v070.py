from alembic import op
import sqlalchemy as sa

revision='0047_workflow_compliance_v070'
down_revision='0046_evidence_integrity_v069'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('workflow_compliance_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('snapshot_date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('snapshot_json', sa.Text(), nullable=False),
        sa.Column('snapshot_hash', sa.String(64), nullable=False),
        sa.Column('generated_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('snapshot_date', name='uq_workflow_compliance_snapshot_date'),
        sa.UniqueConstraint('snapshot_hash', name='uq_workflow_compliance_snapshot_hash'),
    )
    op.create_index('ix_workflow_compliance_snapshot_date', 'workflow_compliance_snapshots', ['snapshot_date'])
    op.create_index('ix_workflow_compliance_snapshot_status', 'workflow_compliance_snapshots', ['status'])
    op.create_index('ix_workflow_compliance_snapshot_hash', 'workflow_compliance_snapshots', ['snapshot_hash'])
    op.create_index('ix_workflow_compliance_snapshot_created', 'workflow_compliance_snapshots', ['created_at'])

def downgrade():
    for n in ['ix_workflow_compliance_snapshot_created','ix_workflow_compliance_snapshot_hash','ix_workflow_compliance_snapshot_status','ix_workflow_compliance_snapshot_date']:
        op.drop_index(n, table_name='workflow_compliance_snapshots')
    op.drop_table('workflow_compliance_snapshots')
