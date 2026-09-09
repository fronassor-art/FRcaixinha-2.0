"""v0.41 executive governance snapshots"""
from alembic import op
import sqlalchemy as sa
revision='0019_governance_v041'
down_revision='0018_financial_reconciliation_v040'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('governance_snapshots',
      sa.Column('id',sa.Integer(),primary_key=True),
      sa.Column('snapshot_date',sa.Date(),nullable=False),
      sa.Column('status',sa.String(20),nullable=False),
      sa.Column('snapshot_json',sa.Text(),nullable=False),
      sa.Column('snapshot_hash',sa.String(64),nullable=False),
      sa.Column('generated_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
      sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
      sa.UniqueConstraint('snapshot_date',name='uq_governance_snapshot_date'),
      sa.UniqueConstraint('snapshot_hash',name='uq_governance_snapshot_hash'))
    op.create_index('ix_governance_snapshots_snapshot_date','governance_snapshots',['snapshot_date'])
    op.create_index('ix_governance_snapshots_status','governance_snapshots',['status'])
    op.create_index('ix_governance_snapshots_snapshot_hash','governance_snapshots',['snapshot_hash'],unique=True)

def downgrade():
    op.drop_index('ix_governance_snapshots_snapshot_hash',table_name='governance_snapshots')
    op.drop_index('ix_governance_snapshots_status',table_name='governance_snapshots')
    op.drop_index('ix_governance_snapshots_snapshot_date',table_name='governance_snapshots')
    op.drop_table('governance_snapshots')
