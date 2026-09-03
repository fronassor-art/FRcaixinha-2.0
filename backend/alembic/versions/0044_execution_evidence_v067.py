from alembic import op
import sqlalchemy as sa
revision='0044_execution_evidence_v067'
down_revision='0043_execution_assist_v066'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('workflow_execution_evidence',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('operational_workflow_tasks.id'), nullable=False),
        sa.Column('added_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('evidence_type', sa.String(20), nullable=False, server_default='NOTE'),
        sa.Column('title', sa.String(160), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('content_hash', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_workflow_evidence_task_id','workflow_execution_evidence',['task_id'])
    op.create_index('ix_workflow_evidence_added_by','workflow_execution_evidence',['added_by'])
    op.create_index('ix_workflow_evidence_content_hash','workflow_execution_evidence',['content_hash'])
    op.create_table('workflow_execution_checklist_items',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('operational_workflow_tasks.id'), nullable=False),
        sa.Column('label', sa.String(240), nullable=False),
        sa.Column('required', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('completed', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('completed_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_workflow_checklist_task_id','workflow_execution_checklist_items',['task_id'])
    op.create_index('ix_workflow_checklist_completed','workflow_execution_checklist_items',['completed'])

def downgrade():
    op.drop_index('ix_workflow_checklist_completed', table_name='workflow_execution_checklist_items')
    op.drop_index('ix_workflow_checklist_task_id', table_name='workflow_execution_checklist_items')
    op.drop_table('workflow_execution_checklist_items')
    op.drop_index('ix_workflow_evidence_content_hash', table_name='workflow_execution_evidence')
    op.drop_index('ix_workflow_evidence_added_by', table_name='workflow_execution_evidence')
    op.drop_index('ix_workflow_evidence_task_id', table_name='workflow_execution_evidence')
    op.drop_table('workflow_execution_evidence')
