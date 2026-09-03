from alembic import op
import sqlalchemy as sa

revision='0033_allocation_transparency_v056'
down_revision='0032_allocation_governance_v055'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('allocation_transparency_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('resource_allocation_snapshot_id', sa.Integer(), sa.ForeignKey('resource_allocation_snapshots.id'), nullable=False),
        sa.Column('group_id', sa.Integer(), sa.ForeignKey('groups.id'), nullable=False),
        sa.Column('policy_version', sa.Integer(), nullable=False),
        sa.Column('policy_snapshot_json', sa.Text(), nullable=False),
        sa.Column('input_snapshot_json', sa.Text(), nullable=False),
        sa.Column('explanation_json', sa.Text(), nullable=False),
        sa.Column('explanation_hash', sa.String(64), nullable=False),
        sa.Column('generated_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_allocation_transparency_snapshots_resource_allocation_snapshot_id', 'allocation_transparency_snapshots', ['resource_allocation_snapshot_id'], unique=True)
    op.create_index('ix_allocation_transparency_snapshots_group_id', 'allocation_transparency_snapshots', ['group_id'])
    op.create_index('ix_allocation_transparency_snapshots_explanation_hash', 'allocation_transparency_snapshots', ['explanation_hash'], unique=True)
    op.create_index('ix_allocation_transparency_snapshots_created_at', 'allocation_transparency_snapshots', ['created_at'])

def downgrade():
    op.drop_index('ix_allocation_transparency_snapshots_created_at', table_name='allocation_transparency_snapshots')
    op.drop_index('ix_allocation_transparency_snapshots_explanation_hash', table_name='allocation_transparency_snapshots')
    op.drop_index('ix_allocation_transparency_snapshots_group_id', table_name='allocation_transparency_snapshots')
    op.drop_index('ix_allocation_transparency_snapshots_resource_allocation_snapshot_id', table_name='allocation_transparency_snapshots')
    op.drop_table('allocation_transparency_snapshots')
