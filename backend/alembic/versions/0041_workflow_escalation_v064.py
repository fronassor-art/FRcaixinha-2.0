from alembic import op
import sqlalchemy as sa
revision='0041_workflow_escalation_v064'
down_revision='0040_workflow_sla_v063'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('operational_action_records', sa.Column('source_task_id', sa.Integer(), nullable=True))
    op.add_column('operational_action_records', sa.Column('escalation_level', sa.String(20), nullable=False, server_default='NONE'))
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("operational_action_records") as batch_op:
            batch_op.create_foreign_key(
                "fk_operational_action_records_source_task",
                "operational_workflow_tasks",
                ["source_task_id"],
                ["id"],
            )
    else:
        op.create_foreign_key('fk_operational_action_records_source_task', 'operational_action_records', 'operational_workflow_tasks', ['source_task_id'], ['id'])
    op.create_index('ix_operational_action_records_source_task_id', 'operational_action_records', ['source_task_id'])
    op.create_index('ix_operational_action_records_escalation_level', 'operational_action_records', ['escalation_level'])

def downgrade():
    op.drop_index('ix_operational_action_records_escalation_level', table_name='operational_action_records')
    op.drop_index('ix_operational_action_records_source_task_id', table_name='operational_action_records')
    op.drop_constraint('fk_operational_action_records_source_task', 'operational_action_records', type_='foreignkey')
    op.drop_column('operational_action_records', 'escalation_level')
    op.drop_column('operational_action_records', 'source_task_id')
