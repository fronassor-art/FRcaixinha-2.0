from alembic import op
import sqlalchemy as sa
revision='0035_integrated_governance_v058'
down_revision='0034_allocation_decision_governance_v057'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('integrated_governance_snapshots',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('group_id',sa.Integer(),sa.ForeignKey('groups.id'),nullable=False),
        sa.Column('loan_id',sa.Integer(),sa.ForeignKey('loans.id'),nullable=False),
        sa.Column('member_id',sa.Integer(),sa.ForeignKey('members.id'),nullable=False),
        sa.Column('final_decision',sa.String(20),nullable=False),
        sa.Column('scenario',sa.String(30),nullable=False),
        sa.Column('horizon_months',sa.Integer(),nullable=False),
        sa.Column('snapshot_json',sa.Text(),nullable=False),
        sa.Column('snapshot_hash',sa.String(64),nullable=False),
        sa.Column('generated_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_integrated_governance_snapshots_group_id','integrated_governance_snapshots',['group_id'])
    op.create_index('ix_integrated_governance_snapshots_loan_id','integrated_governance_snapshots',['loan_id'])
    op.create_index('ix_integrated_governance_snapshots_member_id','integrated_governance_snapshots',['member_id'])
    op.create_index('ix_integrated_governance_snapshots_final_decision','integrated_governance_snapshots',['final_decision'])
    op.create_index('ix_integrated_governance_snapshots_snapshot_hash','integrated_governance_snapshots',['snapshot_hash'],unique=True)
    op.create_index('ix_integrated_governance_snapshots_created_at','integrated_governance_snapshots',['created_at'])

def downgrade():
    for n in ['ix_integrated_governance_snapshots_created_at','ix_integrated_governance_snapshots_snapshot_hash','ix_integrated_governance_snapshots_final_decision','ix_integrated_governance_snapshots_member_id','ix_integrated_governance_snapshots_loan_id','ix_integrated_governance_snapshots_group_id']:
        op.drop_index(n,table_name='integrated_governance_snapshots')
    op.drop_table('integrated_governance_snapshots')
