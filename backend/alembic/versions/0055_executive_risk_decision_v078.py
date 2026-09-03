from alembic import op
import sqlalchemy as sa
revision='0055_executive_risk_decision_v078'; down_revision='0054_executive_risk_response_v077'; branch_labels=None; depends_on=None

def upgrade():
    op.create_table('executive_risk_decisions',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('snapshot_id',sa.Integer(),sa.ForeignKey('executive_risk_response_snapshots.id'),nullable=True),
        sa.Column('alert_id',sa.Integer(),sa.ForeignKey('operational_risk_alerts.id'),nullable=True),
        sa.Column('response_plan_id',sa.Integer(),sa.ForeignKey('operational_risk_response_plans.id'),nullable=True),
        sa.Column('status',sa.String(20),nullable=False), sa.Column('priority',sa.String(20),nullable=False),
        sa.Column('decision_type',sa.String(40),nullable=False), sa.Column('recommendation',sa.Text(),nullable=False),
        sa.Column('rationale',sa.Text(),nullable=True), sa.Column('decision',sa.String(30),nullable=True),
        sa.Column('conditions',sa.Text(),nullable=True), sa.Column('requested_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
        sa.Column('decided_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True), sa.Column('decided_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('decision_hash',sa.String(64),nullable=False), sa.Column('created_at',sa.DateTime(timezone=True),nullable=False), sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_exec_risk_decision_snapshot','executive_risk_decisions',['snapshot_id']); op.create_index('ix_exec_risk_decision_alert','executive_risk_decisions',['alert_id']); op.create_index('ix_exec_risk_decision_plan','executive_risk_decisions',['response_plan_id']); op.create_index('ix_exec_risk_decision_status','executive_risk_decisions',['status']); op.create_index('ix_exec_risk_decision_priority','executive_risk_decisions',['priority']); op.create_index('ix_exec_risk_decision_hash','executive_risk_decisions',['decision_hash'],unique=True); op.create_index('ix_exec_risk_decision_created','executive_risk_decisions',['created_at'])

def downgrade():
    for name in ['ix_exec_risk_decision_created','ix_exec_risk_decision_hash','ix_exec_risk_decision_priority','ix_exec_risk_decision_status','ix_exec_risk_decision_plan','ix_exec_risk_decision_alert','ix_exec_risk_decision_snapshot']:
        op.drop_index(name,table_name='executive_risk_decisions')
    op.drop_table('executive_risk_decisions')
