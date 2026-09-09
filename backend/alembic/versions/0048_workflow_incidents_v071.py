from alembic import op
import sqlalchemy as sa
revision='0048_workflow_incidents_v071'; down_revision='0047_workflow_compliance_v070'; branch_labels=None; depends_on=None
def upgrade():
    op.create_table('workflow_incidents',
      sa.Column('id',sa.Integer(),primary_key=True), sa.Column('check_code',sa.String(80),nullable=False),
      sa.Column('severity',sa.String(20),nullable=False), sa.Column('status',sa.String(24),nullable=False),
      sa.Column('title',sa.String(200),nullable=False), sa.Column('description',sa.Text(),nullable=True),
      sa.Column('assigned_to',sa.Integer(),sa.ForeignKey('users.id'),nullable=True), sa.Column('due_at',sa.DateTime(timezone=True),nullable=True),
      sa.Column('remediation_plan',sa.Text(),nullable=True), sa.Column('resolution',sa.Text(),nullable=True),
      sa.Column('opened_at',sa.DateTime(timezone=True),nullable=False), sa.Column('closed_at',sa.DateTime(timezone=True),nullable=True),
      sa.Column('created_at',sa.DateTime(timezone=True),nullable=False), sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    for n,c in [('ix_wi_check','check_code'),('ix_wi_severity','severity'),('ix_wi_status','status'),('ix_wi_assigned','assigned_to'),('ix_wi_due','due_at'),('ix_wi_opened','opened_at'),('ix_wi_updated','updated_at')]: op.create_index(n,'workflow_incidents',[c])
def downgrade():
    for n in ['ix_wi_updated','ix_wi_opened','ix_wi_due','ix_wi_assigned','ix_wi_status','ix_wi_severity','ix_wi_check']: op.drop_index(n,table_name='workflow_incidents')
    op.drop_table('workflow_incidents')
