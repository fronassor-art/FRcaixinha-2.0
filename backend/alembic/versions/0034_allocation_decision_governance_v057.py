from alembic import op
import sqlalchemy as sa
revision='0034_allocation_decision_governance_v057'
down_revision='0033_allocation_transparency_v056'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('allocation_decision_records',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('transparency_snapshot_id',sa.Integer(),sa.ForeignKey('allocation_transparency_snapshots.id'),nullable=False),
        sa.Column('group_id',sa.Integer(),sa.ForeignKey('groups.id'),nullable=False),
        sa.Column('requested_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
        sa.Column('analyzed_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('decided_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('decision',sa.String(20),nullable=False),
        sa.Column('policy_version',sa.Integer(),nullable=False),
        sa.Column('transparency_hash',sa.String(64),nullable=False),
        sa.Column('decision_input_hash',sa.String(64),nullable=False),
        sa.Column('exception_applied',sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column('exception_reason',sa.Text(),nullable=True),
        sa.Column('admin_note',sa.Text(),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_allocation_decision_records_transparency_snapshot_id','allocation_decision_records',['transparency_snapshot_id'])
    op.create_index('ix_allocation_decision_records_group_id','allocation_decision_records',['group_id'])
    op.create_index('ix_allocation_decision_records_decision','allocation_decision_records',['decision'])
    op.create_index('ix_allocation_decision_records_decision_input_hash','allocation_decision_records',['decision_input_hash'],unique=True)
    op.create_index('ix_allocation_decision_records_created_at','allocation_decision_records',['created_at'])

def downgrade():
    for n in ['ix_allocation_decision_records_created_at','ix_allocation_decision_records_decision_input_hash','ix_allocation_decision_records_decision','ix_allocation_decision_records_group_id','ix_allocation_decision_records_transparency_snapshot_id']:
        op.drop_index(n,table_name='allocation_decision_records')
    op.drop_table('allocation_decision_records')
