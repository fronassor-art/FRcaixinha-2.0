from alembic import op
import sqlalchemy as sa
revision='0058_executive_risk_effectiveness_v081'; down_revision='0057_executive_risk_execution_v080'; branch_labels=None; depends_on=None
def upgrade():
    op.create_table('executive_risk_effectiveness',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('execution_id',sa.Integer(),sa.ForeignKey('executive_risk_decision_executions.id'),nullable=False),
        sa.Column('status',sa.String(20),nullable=False),
        sa.Column('indicator_code',sa.String(60),nullable=False),
        sa.Column('baseline_score',sa.Float(),nullable=True),
        sa.Column('followup_score',sa.Float(),nullable=True),
        sa.Column('delta_score',sa.Float(),nullable=True),
        sa.Column('effectiveness_criteria',sa.Text(),nullable=False),
        sa.Column('effectiveness_result',sa.String(20),nullable=True),
        sa.Column('notes',sa.Text(),nullable=True),
        sa.Column('reviewed_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
        sa.Column('reviewed_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('integrity_hash',sa.String(64),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_unique_constraint('uq_exec_risk_effectiveness_execution','executive_risk_effectiveness',['execution_id'])
    op.create_index('ix_exec_risk_eff_status','executive_risk_effectiveness',['status'])
    op.create_index('ix_exec_risk_eff_hash','executive_risk_effectiveness',['integrity_hash'],unique=True)
    op.create_index('ix_exec_risk_eff_created','executive_risk_effectiveness',['created_at'])
def downgrade():
    for n in ['ix_exec_risk_eff_created','ix_exec_risk_eff_hash','ix_exec_risk_eff_status']:
        op.drop_index(n,table_name='executive_risk_effectiveness')
    op.drop_constraint('uq_exec_risk_effectiveness_execution','executive_risk_effectiveness',type_='unique')
    op.drop_table('executive_risk_effectiveness')
