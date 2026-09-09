from alembic import op
import sqlalchemy as sa
revision='0059_continuous_improvement_v082'; down_revision='0058_executive_risk_effectiveness_v081'; branch_labels=None; depends_on=None
def upgrade():
    op.create_table('continuous_improvement_recommendations',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('indicator_code',sa.String(60),nullable=False),
        sa.Column('pattern_code',sa.String(50),nullable=False),
        sa.Column('status',sa.String(20),nullable=False),
        sa.Column('sample_size',sa.Integer(),nullable=False),
        sa.Column('effective_count',sa.Integer(),nullable=False),
        sa.Column('partial_count',sa.Integer(),nullable=False),
        sa.Column('ineffective_count',sa.Integer(),nullable=False),
        sa.Column('avg_delta',sa.Float(),nullable=True),
        sa.Column('recommendation',sa.Text(),nullable=False),
        sa.Column('decision',sa.String(20),nullable=True),
        sa.Column('decision_note',sa.Text(),nullable=True),
        sa.Column('decided_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
        sa.Column('decided_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('implementation_note',sa.Text(),nullable=True),
        sa.Column('implemented_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
        sa.Column('implemented_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('integrity_hash',sa.String(64),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_ci_indicator','continuous_improvement_recommendations',['indicator_code'])
    op.create_index('ix_ci_pattern','continuous_improvement_recommendations',['pattern_code'])
    op.create_index('ix_ci_status','continuous_improvement_recommendations',['status'])
    op.create_index('ix_ci_hash','continuous_improvement_recommendations',['integrity_hash'],unique=True)
    op.create_index('ix_ci_created','continuous_improvement_recommendations',['created_at'])
def downgrade():
    for n in ['ix_ci_created','ix_ci_hash','ix_ci_status','ix_ci_pattern','ix_ci_indicator']:
        op.drop_index(n,table_name='continuous_improvement_recommendations')
    op.drop_table('continuous_improvement_recommendations')
