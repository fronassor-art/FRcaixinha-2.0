from alembic import op
import sqlalchemy as sa
revision='0050_capa_effectiveness_v073'; down_revision='0049_capa_v072'; branch_labels=None; depends_on=None

def upgrade():
    op.create_table('capa_effectiveness_reviews',
      sa.Column('id',sa.Integer(),primary_key=True),sa.Column('capa_id',sa.Integer(),sa.ForeignKey('corrective_action_plans.id'),nullable=False),sa.Column('result',sa.Text(),nullable=False),sa.Column('score',sa.Integer(),nullable=True),sa.Column('notes',sa.Text(),nullable=True),sa.Column('reviewed_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),sa.Column('reviewed_at',sa.DateTime(timezone=True),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_capa_review_capa','capa_effectiveness_reviews',['capa_id']); op.create_index('ix_capa_review_date','capa_effectiveness_reviews',['reviewed_at'])
    op.create_table('capa_recurrence_events',
      sa.Column('id',sa.Integer(),primary_key=True),sa.Column('capa_id',sa.Integer(),sa.ForeignKey('corrective_action_plans.id'),nullable=False),sa.Column('incident_id',sa.Integer(),sa.ForeignKey('workflow_incidents.id'),nullable=False),sa.Column('source_check_code',sa.String(80),nullable=False),sa.Column('severity',sa.String(20),nullable=False),sa.Column('detected_at',sa.DateTime(timezone=True),nullable=False),sa.Column('notes',sa.Text(),nullable=True),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint('capa_id','incident_id',name='uq_capa_recurrence_capa_incident'))
    op.create_index('ix_capa_recurrence_capa','capa_recurrence_events',['capa_id']); op.create_index('ix_capa_recurrence_incident','capa_recurrence_events',['incident_id']); op.create_index('ix_capa_recurrence_date','capa_recurrence_events',['detected_at'])

def downgrade():
    for n in ['ix_capa_recurrence_date','ix_capa_recurrence_incident','ix_capa_recurrence_capa']: op.drop_index(n,table_name='capa_recurrence_events')
    op.drop_table('capa_recurrence_events')
    for n in ['ix_capa_review_date','ix_capa_review_capa']: op.drop_index(n,table_name='capa_effectiveness_reviews')
    op.drop_table('capa_effectiveness_reviews')
