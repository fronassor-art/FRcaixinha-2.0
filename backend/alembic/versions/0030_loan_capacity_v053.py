from alembic import op
import sqlalchemy as sa
revision='0030_loan_capacity_v053'; down_revision='0029_scenario_simulation_v052'; branch_labels=None; depends_on=None

def upgrade():
    op.create_table('loan_capacity_snapshots',
      sa.Column('id',sa.Integer(),primary_key=True),
      sa.Column('group_id',sa.Integer(),sa.ForeignKey('groups.id'),nullable=True),
      sa.Column('member_id',sa.Integer(),sa.ForeignKey('members.id'),nullable=True),
      sa.Column('as_of_date',sa.Date(),nullable=False),
      sa.Column('horizon_months',sa.Integer(),nullable=False),
      sa.Column('scenario',sa.String(20),nullable=False),
      sa.Column('decision',sa.String(20),nullable=False),
      sa.Column('capacity',sa.Numeric(14,2),nullable=False),
      sa.Column('snapshot_json',sa.Text(),nullable=False),
      sa.Column('snapshot_hash',sa.String(64),nullable=False),
      sa.Column('generated_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
      sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_loan_capacity_snapshots_group_id','loan_capacity_snapshots',['group_id'])
    op.create_index('ix_loan_capacity_snapshots_member_id','loan_capacity_snapshots',['member_id'])
    op.create_index('ix_loan_capacity_snapshots_as_of_date','loan_capacity_snapshots',['as_of_date'])
    op.create_index('ix_loan_capacity_snapshots_scenario','loan_capacity_snapshots',['scenario'])
    op.create_index('ix_loan_capacity_snapshots_decision','loan_capacity_snapshots',['decision'])
    op.create_index('ix_loan_capacity_snapshots_hash','loan_capacity_snapshots',['snapshot_hash'],unique=True)

def downgrade():
    op.drop_index('ix_loan_capacity_snapshots_hash',table_name='loan_capacity_snapshots')
    op.drop_index('ix_loan_capacity_snapshots_decision',table_name='loan_capacity_snapshots')
    op.drop_index('ix_loan_capacity_snapshots_scenario',table_name='loan_capacity_snapshots')
    op.drop_index('ix_loan_capacity_snapshots_as_of_date',table_name='loan_capacity_snapshots')
    op.drop_index('ix_loan_capacity_snapshots_member_id',table_name='loan_capacity_snapshots')
    op.drop_index('ix_loan_capacity_snapshots_group_id',table_name='loan_capacity_snapshots')
    op.drop_table('loan_capacity_snapshots')
