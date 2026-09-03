from alembic import op
import sqlalchemy as sa
revision='0046_evidence_integrity_v069'
down_revision='0045_evidence_storage_v068'
branch_labels=None
depends_on=None
def upgrade():
    op.create_table('workflow_evidence_integrity_events',
      sa.Column('id',sa.Integer(),primary_key=True),sa.Column('file_id',sa.Integer(),sa.ForeignKey('workflow_execution_evidence_files.id'),nullable=False),sa.Column('task_id',sa.Integer(),sa.ForeignKey('operational_workflow_tasks.id'),nullable=False),sa.Column('event_type',sa.String(32),nullable=False),sa.Column('expected_sha256',sa.String(64),nullable=False),sa.Column('observed_sha256',sa.String(64),nullable=True),sa.Column('status',sa.String(16),nullable=False),sa.Column('actor_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),sa.Column('previous_event_hash',sa.String(64),nullable=True),sa.Column('event_hash',sa.String(64),nullable=False),sa.Column('details',sa.Text(),nullable=True),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    for name,col in [('ix_weie_file','file_id'),('ix_weie_task','task_id'),('ix_weie_event_type','event_type'),('ix_weie_status','status'),('ix_weie_actor','actor_id'),('ix_weie_event_hash','event_hash'),('ix_weie_created','created_at')]: op.create_index(name,'workflow_evidence_integrity_events',[col])
def downgrade():
    for n in ['ix_weie_created','ix_weie_event_hash','ix_weie_actor','ix_weie_status','ix_weie_event_type','ix_weie_task','ix_weie_file']: op.drop_index(n,table_name='workflow_evidence_integrity_events')
    op.drop_table('workflow_evidence_integrity_events')
