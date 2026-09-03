from alembic import op
import sqlalchemy as sa
revision='0039_admin_workflow_v062'; down_revision='0038_command_center_v061'; branch_labels=None; depends_on=None
def upgrade():
    op.create_table('operational_workflow_tasks',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('action_code',sa.String(60),nullable=False),sa.Column('status',sa.String(20),nullable=False),sa.Column('priority',sa.String(20),nullable=False),sa.Column('assigned_to',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),sa.Column('due_at',sa.DateTime(timezone=True),nullable=True),sa.Column('description',sa.Text(),nullable=True),sa.Column('created_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    for n,c,u in [('action','action_code',False),('status','status',False),('priority','priority',False),('assigned','assigned_to',False),('due','due_at',False),('created','created_at',False)]: op.create_index('ix_owt_'+n,'operational_workflow_tasks',[c],unique=u)
    op.create_table('operational_workflow_events',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('task_id',sa.Integer(),sa.ForeignKey('operational_workflow_tasks.id'),nullable=False),sa.Column('actor_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),sa.Column('from_status',sa.String(20),nullable=False),sa.Column('to_status',sa.String(20),nullable=False),sa.Column('note',sa.Text(),nullable=True),sa.Column('evidence',sa.Text(),nullable=True),sa.Column('event_hash',sa.String(64),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    for n,c in [('task','task_id'),('created','created_at'),('hash','event_hash')]: op.create_index('ix_owe_'+n,'operational_workflow_events',[c],unique=False)
def downgrade():
    for n in ['ix_owe_hash','ix_owe_created','ix_owe_task']: op.drop_index(n,table_name='operational_workflow_events')
    op.drop_table('operational_workflow_events')
    for n in ['ix_owt_created','ix_owt_due','ix_owt_assigned','ix_owt_priority','ix_owt_status','ix_owt_action']: op.drop_index(n,table_name='operational_workflow_tasks')
    op.drop_table('operational_workflow_tasks')
