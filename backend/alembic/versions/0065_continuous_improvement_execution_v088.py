from alembic import op
import sqlalchemy as sa
revision='0065_continuous_improvement_execution_v088'; down_revision='0064_continuous_improvement_governance_v087'; branch_labels=None; depends_on=None
def upgrade():
 op.create_table('continuous_improvement_executions',
  sa.Column('id',sa.Integer(),primary_key=True),
  sa.Column('decision_id',sa.Integer(),sa.ForeignKey('continuous_improvement_assignment_decisions.id'),nullable=False),
  sa.Column('recommendation_id',sa.Integer(),sa.ForeignKey('continuous_improvement_recommendations.id'),nullable=False),
  sa.Column('plan_id',sa.Integer(),sa.ForeignKey('continuous_improvement_plans.id'),nullable=False),
  sa.Column('status',sa.String(20),nullable=False),
  sa.Column('assigned_to',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
  sa.Column('started_at',sa.DateTime(timezone=True),nullable=True),
  sa.Column('completed_at',sa.DateTime(timezone=True),nullable=True),
  sa.Column('resolution_note',sa.Text(),nullable=True),
  sa.Column('evidence_note',sa.Text(),nullable=True),
  sa.Column('verified_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
  sa.Column('verified_at',sa.DateTime(timezone=True),nullable=True),
  sa.Column('verification_note',sa.Text(),nullable=True),
  sa.Column('execution_hash',sa.String(64),nullable=False),
  sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
  sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
 for n,c,u in [('ix_ci_exec_decision','decision_id',False),('ix_ci_exec_rec','recommendation_id',False),('ix_ci_exec_plan','plan_id',False),('ix_ci_exec_status','status',False),('ix_ci_exec_assigned','assigned_to',False),('ix_ci_exec_verified','verified_by',False),('ix_ci_exec_hash','execution_hash',True),('ix_ci_exec_created','created_at',False),('ix_ci_exec_updated','updated_at',False)]: op.create_index(n,'continuous_improvement_executions',[c],unique=u)
 op.create_unique_constraint('uq_ci_exec_decision','continuous_improvement_executions',['decision_id'])
def downgrade():
 op.drop_table('continuous_improvement_executions')
