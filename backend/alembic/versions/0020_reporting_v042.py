"""v0.42 executive reporting and accountability snapshots"""
from alembic import op
import sqlalchemy as sa
revision='0020_reporting_v042'
down_revision='0019_governance_v041'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('report_snapshots',
      sa.Column('id',sa.Integer(),primary_key=True),
      sa.Column('report_type',sa.String(30),nullable=False),
      sa.Column('competence',sa.Date(),nullable=False),
      sa.Column('scope_id',sa.Integer(),nullable=True),
      sa.Column('snapshot_json',sa.Text(),nullable=False),
      sa.Column('snapshot_hash',sa.String(64),nullable=False),
      sa.Column('generated_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
      sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
      sa.UniqueConstraint('report_type','competence','scope_id',name='uq_report_snapshot_scope'))
    op.create_index('ix_report_snapshots_type_comp','report_snapshots',['report_type','competence'])
    op.create_index('ix_report_snapshots_hash','report_snapshots',['snapshot_hash'],unique=True)

def downgrade():
    op.drop_index('ix_report_snapshots_hash',table_name='report_snapshots')
    op.drop_index('ix_report_snapshots_type_comp',table_name='report_snapshots')
    op.drop_table('report_snapshots')
