from alembic import op
import sqlalchemy as sa

revision='0045_evidence_storage_v068'
down_revision='0044_execution_evidence_v067'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('workflow_execution_evidence_files',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('evidence_id', sa.Integer(), sa.ForeignKey('workflow_execution_evidence.id'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('original_name', sa.String(255), nullable=False),
        sa.Column('storage_key', sa.String(180), nullable=False),
        sa.Column('content_type', sa.String(120), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('uploaded_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('storage_key', name='uq_workflow_evidence_file_storage_key'),
        sa.UniqueConstraint('evidence_id', 'version', name='uq_workflow_evidence_file_version'),
    )
    op.create_index('ix_workflow_evidence_files_evidence_id','workflow_execution_evidence_files',['evidence_id'])
    op.create_index('ix_workflow_evidence_files_sha256','workflow_execution_evidence_files',['sha256'])
    op.create_index('ix_workflow_evidence_files_uploaded_by','workflow_execution_evidence_files',['uploaded_by'])
    op.create_index('ix_workflow_evidence_files_created_at','workflow_execution_evidence_files',['created_at'])

def downgrade():
    op.drop_index('ix_workflow_evidence_files_created_at', table_name='workflow_execution_evidence_files')
    op.drop_index('ix_workflow_evidence_files_uploaded_by', table_name='workflow_execution_evidence_files')
    op.drop_index('ix_workflow_evidence_files_sha256', table_name='workflow_execution_evidence_files')
    op.drop_index('ix_workflow_evidence_files_evidence_id', table_name='workflow_execution_evidence_files')
    op.drop_table('workflow_execution_evidence_files')
