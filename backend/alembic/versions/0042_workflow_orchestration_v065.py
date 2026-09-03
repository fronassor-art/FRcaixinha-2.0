from alembic import op
import sqlalchemy as sa

revision='0042_workflow_orchestration_v065'
down_revision='0041_workflow_escalation_v064'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table(
        'operational_workflow_orchestrations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('queue_status', sa.String(20), nullable=False, server_default='READY'),
        sa.Column('assigned_to', sa.Integer(), nullable=True),
        sa.Column('priority', sa.String(20), nullable=False),
        sa.Column('sla_status', sa.String(20), nullable=False),
        sa.Column('escalation_level', sa.String(20), nullable=False, server_default='NONE'),
        sa.Column('orchestration_score', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_evaluated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['operational_workflow_tasks.id']),
        sa.ForeignKeyConstraint(['assigned_to'], ['users.id']),
        sa.UniqueConstraint('task_id', name='uq_operational_workflow_orchestrations_task'),
    )
    op.create_index('ix_operational_workflow_orchestrations_queue_status','operational_workflow_orchestrations',['queue_status'])
    op.create_index('ix_operational_workflow_orchestrations_assigned_to','operational_workflow_orchestrations',['assigned_to'])
    op.create_index('ix_operational_workflow_orchestrations_priority','operational_workflow_orchestrations',['priority'])
    op.create_index('ix_operational_workflow_orchestrations_sla_status','operational_workflow_orchestrations',['sla_status'])
    op.create_index('ix_operational_workflow_orchestrations_escalation_level','operational_workflow_orchestrations',['escalation_level'])
    op.create_index('ix_operational_workflow_orchestrations_orchestration_score','operational_workflow_orchestrations',['orchestration_score'])
    op.create_index('ix_operational_workflow_orchestrations_last_evaluated_at','operational_workflow_orchestrations',['last_evaluated_at'])

def downgrade():
    op.drop_index('ix_operational_workflow_orchestrations_last_evaluated_at', table_name='operational_workflow_orchestrations')
    op.drop_index('ix_operational_workflow_orchestrations_orchestration_score', table_name='operational_workflow_orchestrations')
    op.drop_index('ix_operational_workflow_orchestrations_escalation_level', table_name='operational_workflow_orchestrations')
    op.drop_index('ix_operational_workflow_orchestrations_sla_status', table_name='operational_workflow_orchestrations')
    op.drop_index('ix_operational_workflow_orchestrations_priority', table_name='operational_workflow_orchestrations')
    op.drop_index('ix_operational_workflow_orchestrations_assigned_to', table_name='operational_workflow_orchestrations')
    op.drop_index('ix_operational_workflow_orchestrations_queue_status', table_name='operational_workflow_orchestrations')
    op.drop_table('operational_workflow_orchestrations')
