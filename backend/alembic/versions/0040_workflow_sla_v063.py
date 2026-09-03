from alembic import op
import sqlalchemy as sa
revision='0040_workflow_sla_v063'; down_revision='0039_admin_workflow_v062'; branch_labels=None; depends_on=None

def upgrade():
    op.add_column('operational_workflow_tasks', sa.Column('sla_status', sa.String(20), nullable=False, server_default='ON_TRACK'))
    op.add_column('operational_workflow_tasks', sa.Column('escalation_level', sa.String(20), nullable=False, server_default='NONE'))
    op.add_column('operational_workflow_tasks', sa.Column('escalated_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_owt_sla_status','operational_workflow_tasks',['sla_status'])
    op.create_index('ix_owt_escalation_level','operational_workflow_tasks',['escalation_level'])

def downgrade():
    op.drop_index('ix_owt_escalation_level',table_name='operational_workflow_tasks')
    op.drop_index('ix_owt_sla_status',table_name='operational_workflow_tasks')
    op.drop_column('operational_workflow_tasks','escalated_at')
    op.drop_column('operational_workflow_tasks','escalation_level')
    op.drop_column('operational_workflow_tasks','sla_status')
