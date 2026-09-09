from alembic import op
import sqlalchemy as sa
revision='0057_executive_risk_execution_v080'; down_revision='0056_executive_risk_governance_v079'; branch_labels=None; depends_on=None

def upgrade():
    op.create_table('executive_risk_decision_executions',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('governance_id',sa.Integer(),sa.ForeignKey('executive_risk_decision_governance.id'),nullable=False),
        sa.Column('status',sa.String(30),nullable=False), sa.Column('assigned_to',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
        sa.Column('started_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True), sa.Column('started_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('completed_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True), sa.Column('completed_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('evidence_note',sa.Text(),nullable=True), sa.Column('verified_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
        sa.Column('verified_at',sa.DateTime(timezone=True),nullable=True), sa.Column('verification_note',sa.Text(),nullable=True),
        sa.Column('resolution',sa.Text(),nullable=True), sa.Column('execution_hash',sa.String(64),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False), sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_unique_constraint('uq_exec_risk_execution_governance','executive_risk_decision_executions',['governance_id'])
    op.create_index('ix_exec_risk_exec_status','executive_risk_decision_executions',['status'])
    op.create_index('ix_exec_risk_exec_assigned','executive_risk_decision_executions',['assigned_to'])
    op.create_index('ix_exec_risk_exec_hash','executive_risk_decision_executions',['execution_hash'],unique=True)
    op.create_index('ix_exec_risk_exec_created','executive_risk_decision_executions',['created_at'])

def downgrade():
    for n in ['ix_exec_risk_exec_created','ix_exec_risk_exec_hash','ix_exec_risk_exec_assigned','ix_exec_risk_exec_status']:
        op.drop_index(n,table_name='executive_risk_decision_executions')
    op.drop_constraint('uq_exec_risk_execution_governance','executive_risk_decision_executions',type_='unique')
    op.drop_table('executive_risk_decision_executions')
