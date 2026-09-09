from alembic import op
import sqlalchemy as sa
revision='0056_executive_risk_governance_v079'; down_revision='0055_executive_risk_decision_v078'; branch_labels=None; depends_on=None

def upgrade():
    op.create_table('executive_risk_decision_governance',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('decision_id',sa.Integer(),sa.ForeignKey('executive_risk_decisions.id'),nullable=False),
        sa.Column('required_approvals',sa.Integer(),nullable=False), sa.Column('approvals_count',sa.Integer(),nullable=False),
        sa.Column('status',sa.String(30),nullable=False), sa.Column('conflict_status',sa.String(20),nullable=False),
        sa.Column('conditions_required',sa.Boolean(),nullable=False),
        sa.Column('primary_approver_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
        sa.Column('secondary_approver_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
        sa.Column('validation_status',sa.String(20),nullable=False), sa.Column('validated_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
        sa.Column('validated_at',sa.DateTime(timezone=True),nullable=True), sa.Column('integrity_hash',sa.String(64),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False), sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_unique_constraint('uq_exec_risk_gov_decision','executive_risk_decision_governance',['decision_id'])
    op.create_index('ix_exec_risk_gov_status','executive_risk_decision_governance',['status'])
    op.create_index('ix_exec_risk_gov_validation','executive_risk_decision_governance',['validation_status'])
    op.create_index('ix_exec_risk_gov_hash','executive_risk_decision_governance',['integrity_hash'],unique=True)

def downgrade():
    op.drop_index('ix_exec_risk_gov_hash',table_name='executive_risk_decision_governance')
    op.drop_index('ix_exec_risk_gov_validation',table_name='executive_risk_decision_governance')
    op.drop_index('ix_exec_risk_gov_status',table_name='executive_risk_decision_governance')
    op.drop_constraint('uq_exec_risk_gov_decision', 'executive_risk_decision_governance', type_='unique')
    op.drop_table('executive_risk_decision_governance')
