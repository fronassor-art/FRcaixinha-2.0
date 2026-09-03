from alembic import op
import sqlalchemy as sa
revision='0064_continuous_improvement_governance_v087'; down_revision='0063_continuous_improvement_balancing_v086'; branch_labels=None; depends_on=None
def upgrade():
 op.create_table('continuous_improvement_assignment_decisions',
  sa.Column('id',sa.Integer(),primary_key=True),
  sa.Column('snapshot_id',sa.Integer(),sa.ForeignKey('continuous_improvement_assignment_snapshots.id'),nullable=False),
  sa.Column('recommendation_id',sa.Integer(),sa.ForeignKey('continuous_improvement_recommendations.id'),nullable=False),
  sa.Column('target_user_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
  sa.Column('decision',sa.String(20),nullable=False),
  sa.Column('decision_note',sa.Text(),nullable=False),
  sa.Column('decided_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
  sa.Column('decided_at',sa.DateTime(timezone=True),nullable=False),
  sa.Column('decision_hash',sa.String(64),nullable=False),
  sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
  sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
 op.create_index('ix_ci_gov_snapshot','continuous_improvement_assignment_decisions',['snapshot_id'])
 op.create_index('ix_ci_gov_rec','continuous_improvement_assignment_decisions',['recommendation_id'])
 op.create_index('ix_ci_gov_target','continuous_improvement_assignment_decisions',['target_user_id'])
 op.create_index('ix_ci_gov_decision','continuous_improvement_assignment_decisions',['decision'])
 op.create_index('ix_ci_gov_actor','continuous_improvement_assignment_decisions',['decided_by'])
 op.create_index('ix_ci_gov_decided_at','continuous_improvement_assignment_decisions',['decided_at'])
 op.create_index('ix_ci_gov_hash','continuous_improvement_assignment_decisions',['decision_hash'],unique=True)
def downgrade():
 for n in ['ix_ci_gov_hash','ix_ci_gov_decided_at','ix_ci_gov_actor','ix_ci_gov_decision','ix_ci_gov_target','ix_ci_gov_rec','ix_ci_gov_snapshot']:
  op.drop_index(n,table_name='continuous_improvement_assignment_decisions')
 op.drop_table('continuous_improvement_assignment_decisions')
