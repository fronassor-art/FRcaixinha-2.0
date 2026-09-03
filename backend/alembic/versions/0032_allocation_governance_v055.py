from alembic import op
import sqlalchemy as sa
revision='0032_allocation_governance_v055'; down_revision='0031_resource_allocation_v054'; branch_labels=None; depends_on=None

def upgrade():
    op.create_table('allocation_policies',
      sa.Column('id',sa.Integer(),primary_key=True), sa.Column('group_id',sa.Integer(),sa.ForeignKey('groups.id'),nullable=False),
      sa.Column('name',sa.String(120),nullable=False), sa.Column('quota_weight',sa.Numeric(8,3),nullable=False),
      sa.Column('payment_history_weight',sa.Numeric(8,3),nullable=False), sa.Column('tenure_weight',sa.Numeric(8,3),nullable=False),
      sa.Column('risk_weight',sa.Numeric(8,3),nullable=False), sa.Column('review_factor',sa.Numeric(6,3),nullable=False),
      sa.Column('tie_breaker',sa.String(30),nullable=False), sa.Column('version',sa.Integer(),nullable=False),
      sa.Column('active',sa.Boolean(),nullable=False), sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
      sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_allocation_policies_group_id','allocation_policies',['group_id'],unique=True)

def downgrade():
    op.drop_index('ix_allocation_policies_group_id',table_name='allocation_policies'); op.drop_table('allocation_policies')
