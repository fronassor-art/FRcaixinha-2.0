"""credit policy v0.37"""
from alembic import op
import sqlalchemy as sa
revision='0015_credit_policy_v037'
down_revision='0014_risk_limits_v036'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('groups', sa.Column('max_simultaneous_loans', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('groups', sa.Column('max_installments', sa.Integer(), nullable=False, server_default='12'))
    op.add_column('groups', sa.Column('grace_days', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('groups', sa.Column('min_on_time_ratio', sa.Numeric(6,5), nullable=True))
    op.add_column('groups', sa.Column('max_overdue_installments', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('groups', sa.Column('max_installment_income_ratio', sa.Numeric(6,5), nullable=True))
    op.add_column('groups', sa.Column('max_quota_multiple', sa.Numeric(10,2), nullable=True))
    op.add_column('members', sa.Column('declared_monthly_income', sa.Numeric(14,2), nullable=True))
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        with op.batch_alter_table('groups') as batch_op:
            batch_op.alter_column(
                'max_simultaneous_loans',
                existing_type=sa.Integer(),
                existing_nullable=False,
                server_default=None,
            )
    else:
        op.alter_column('groups', 'max_simultaneous_loans', server_default=None)
    if bind.dialect.name == 'sqlite':
        with op.batch_alter_table('groups') as batch_op:
            batch_op.alter_column(
                'max_installments',
                existing_type=sa.Integer(),
                existing_nullable=False,
                server_default=None,
            )
    else:
        op.alter_column('groups', 'max_installments', server_default=None)
    if bind.dialect.name == 'sqlite':
        with op.batch_alter_table('groups') as batch_op:
            batch_op.alter_column(
                'grace_days',
                existing_type=sa.Integer(),
                existing_nullable=False,
                server_default=None,
            )
    else:
        op.alter_column('groups', 'grace_days', server_default=None)
    if bind.dialect.name == 'sqlite':
        with op.batch_alter_table('groups') as batch_op:
            batch_op.alter_column(
                'max_overdue_installments',
                existing_type=sa.Integer(),
                existing_nullable=False,
                server_default=None,
            )
    else:
        op.alter_column('groups', 'max_overdue_installments', server_default=None)

def downgrade():
    op.drop_column('members','declared_monthly_income')
    for col in ['max_quota_multiple','max_installment_income_ratio','max_overdue_installments','min_on_time_ratio','grace_days','max_installments','max_simultaneous_loans']:
        op.drop_column('groups',col)
