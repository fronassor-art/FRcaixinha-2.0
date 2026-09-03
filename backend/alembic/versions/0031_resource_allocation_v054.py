"""resource allocation v0.54"""
from alembic import op
import sqlalchemy as sa
revision='0031_resource_allocation_v054'
down_revision='0030_loan_capacity_v053'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('resource_allocation_snapshots',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('group_id',sa.Integer(),sa.ForeignKey('groups.id'),nullable=False),
        sa.Column('capacity',sa.Numeric(14,2),nullable=False),
        sa.Column('allocated_total',sa.Numeric(14,2),nullable=False),
        sa.Column('decision',sa.String(20),nullable=False),
        sa.Column('method',sa.String(50),nullable=False),
        sa.Column('snapshot_json',sa.Text(),nullable=False),
        sa.Column('snapshot_hash',sa.String(64),nullable=False),
        sa.Column('generated_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
    )
    op.create_index('ix_resource_allocation_snapshots_group_id','resource_allocation_snapshots',['group_id'])
    op.create_index('ix_resource_allocation_snapshots_decision','resource_allocation_snapshots',['decision'])
    op.create_index('ix_resource_allocation_snapshots_snapshot_hash','resource_allocation_snapshots',['snapshot_hash'],unique=True)

def downgrade():
    op.drop_table('resource_allocation_snapshots')
