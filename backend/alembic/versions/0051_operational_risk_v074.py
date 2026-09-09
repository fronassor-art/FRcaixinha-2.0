from alembic import op
import sqlalchemy as sa
revision='0051_operational_risk_v074'; down_revision='0050_capa_effectiveness_v073'; branch_labels=None; depends_on=None
def upgrade():
    op.create_table('operational_risk_trend_snapshots',
      sa.Column('id',sa.Integer(),primary_key=True),
      sa.Column('snapshot_date',sa.Date(),nullable=False),
      sa.Column('status',sa.String(20),nullable=False),
      sa.Column('risk_score',sa.Integer(),nullable=False),
      sa.Column('snapshot_json',sa.Text(),nullable=False),
      sa.Column('snapshot_hash',sa.String(64),nullable=False),
      sa.Column('generated_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
      sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
      sa.UniqueConstraint('snapshot_date',name='uq_operational_risk_snapshot_date'),
      sa.UniqueConstraint('snapshot_hash',name='uq_operational_risk_snapshot_hash'))
    op.create_index('ix_operational_risk_snapshot_status','operational_risk_trend_snapshots',['status'])
    op.create_index('ix_operational_risk_snapshot_score','operational_risk_trend_snapshots',['risk_score'])
    op.create_index('ix_operational_risk_snapshot_created','operational_risk_trend_snapshots',['created_at'])
def downgrade():
    for n in ['ix_operational_risk_snapshot_created','ix_operational_risk_snapshot_score','ix_operational_risk_snapshot_status']: op.drop_index(n,table_name='operational_risk_trend_snapshots')
    op.drop_table('operational_risk_trend_snapshots')
