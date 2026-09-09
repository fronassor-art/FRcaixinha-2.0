from alembic import op
import sqlalchemy as sa

revision='0043_execution_assist_v066'
down_revision='0042_workflow_orchestration_v065'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('operational_workflow_orchestrations', sa.Column('execution_state', sa.String(24), nullable=False, server_default='PENDING_ACCEPTANCE'))
    op.add_column('operational_workflow_orchestrations', sa.Column('accepted_by', sa.Integer(), nullable=True))
    op.add_column('operational_workflow_orchestrations', sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('operational_workflow_orchestrations', sa.Column('started_by', sa.Integer(), nullable=True))
    op.add_column('operational_workflow_orchestrations', sa.Column('started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('operational_workflow_orchestrations', sa.Column('completed_by', sa.Integer(), nullable=True))
    op.add_column('operational_workflow_orchestrations', sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key('fk_workflow_orch_accepted_by_users','operational_workflow_orchestrations','users',['accepted_by'],['id'])
    op.create_foreign_key('fk_workflow_orch_started_by_users','operational_workflow_orchestrations','users',['started_by'],['id'])
    op.create_foreign_key('fk_workflow_orch_completed_by_users','operational_workflow_orchestrations','users',['completed_by'],['id'])
    op.create_index('ix_workflow_orch_execution_state','operational_workflow_orchestrations',['execution_state'])

def downgrade():
    op.drop_index('ix_workflow_orch_execution_state', table_name='operational_workflow_orchestrations')
    op.drop_constraint('fk_workflow_orch_completed_by_users', 'operational_workflow_orchestrations', type_='foreignkey')
    op.drop_constraint('fk_workflow_orch_started_by_users', 'operational_workflow_orchestrations', type_='foreignkey')
    op.drop_constraint('fk_workflow_orch_accepted_by_users', 'operational_workflow_orchestrations', type_='foreignkey')
    op.drop_column('operational_workflow_orchestrations','completed_at')
    op.drop_column('operational_workflow_orchestrations','completed_by')
    op.drop_column('operational_workflow_orchestrations','started_at')
    op.drop_column('operational_workflow_orchestrations','started_by')
    op.drop_column('operational_workflow_orchestrations','accepted_at')
    op.drop_column('operational_workflow_orchestrations','accepted_by')
    op.drop_column('operational_workflow_orchestrations','execution_state')
