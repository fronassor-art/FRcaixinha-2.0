from alembic import op
import sqlalchemy as sa
revision='0060_continuous_improvement_tracking_v083'; down_revision='0059_continuous_improvement_v082'; branch_labels=None; depends_on=None
def upgrade():
    op.create_table('continuous_improvement_plans',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('recommendation_id',sa.Integer(),sa.ForeignKey('continuous_improvement_recommendations.id'),nullable=False,unique=True),
        sa.Column('status',sa.String(20),nullable=False), sa.Column('indicator_code',sa.String(60),nullable=False),
        sa.Column('baseline_value',sa.Float(),nullable=True), sa.Column('target_value',sa.Float(),nullable=True),
        sa.Column('target_direction',sa.String(20),nullable=False), sa.Column('objective',sa.Text(),nullable=False),
        sa.Column('assigned_to',sa.Integer(),sa.ForeignKey('users.id'),nullable=True), sa.Column('due_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('implementation_note',sa.Text(),nullable=True), sa.Column('implemented_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('closed_at',sa.DateTime(timezone=True),nullable=True), sa.Column('integrity_hash',sa.String(64),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False), sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_ci_plan_rec','continuous_improvement_plans',['recommendation_id'],unique=True); op.create_index('ix_ci_plan_status','continuous_improvement_plans',['status']); op.create_index('ix_ci_plan_indicator','continuous_improvement_plans',['indicator_code']); op.create_index('ix_ci_plan_assigned','continuous_improvement_plans',['assigned_to']); op.create_index('ix_ci_plan_hash','continuous_improvement_plans',['integrity_hash'],unique=True); op.create_index('ix_ci_plan_created','continuous_improvement_plans',['created_at'])
    op.create_table('continuous_improvement_measurements',
        sa.Column('id',sa.Integer(),primary_key=True), sa.Column('plan_id',sa.Integer(),sa.ForeignKey('continuous_improvement_plans.id'),nullable=False),
        sa.Column('measurement_type',sa.String(30),nullable=False), sa.Column('value',sa.Float(),nullable=False), sa.Column('baseline_value',sa.Float(),nullable=True), sa.Column('delta',sa.Float(),nullable=True),
        sa.Column('result',sa.String(20),nullable=False), sa.Column('evidence_note',sa.Text(),nullable=False), sa.Column('measured_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=False), sa.Column('measured_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('verified_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True), sa.Column('verified_at',sa.DateTime(timezone=True),nullable=True), sa.Column('verification_note',sa.Text(),nullable=True),
        sa.Column('integrity_hash',sa.String(64),nullable=False), sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_ci_measure_plan','continuous_improvement_measurements',['plan_id']); op.create_index('ix_ci_measure_hash','continuous_improvement_measurements',['integrity_hash'],unique=True); op.create_index('ix_ci_measure_created','continuous_improvement_measurements',['created_at'])
def downgrade():
    for n in ['ix_ci_measure_created','ix_ci_measure_hash','ix_ci_measure_plan']: op.drop_index(n,table_name='continuous_improvement_measurements')
    op.drop_table('continuous_improvement_measurements')
    for n in ['ix_ci_plan_created','ix_ci_plan_hash','ix_ci_plan_assigned','ix_ci_plan_indicator','ix_ci_plan_status','ix_ci_plan_rec']: op.drop_index(n,table_name='continuous_improvement_plans')
    op.drop_table('continuous_improvement_plans')
