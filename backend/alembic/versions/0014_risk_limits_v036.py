"""risk limits v0.36"""
from alembic import op
import sqlalchemy as sa

revision = '0014_risk_limits_v036'
down_revision = '0013_ledger_hardening_v035'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('groups', sa.Column('min_cash_reserve', sa.Numeric(14,2), nullable=False, server_default='0.00'))
    op.add_column('groups', sa.Column('max_member_exposure', sa.Numeric(14,2), nullable=True))
    op.add_column('groups', sa.Column('max_global_exposure', sa.Numeric(14,2), nullable=True))
    op.add_column('groups', sa.Column('max_exposure_ratio', sa.Numeric(8,5), nullable=True))
    op.execute(sa.text("UPDATE groups SET min_cash_reserve = 0.00 WHERE min_cash_reserve IS NULL"))
    if op.get_bind().dialect.name == 'sqlite':
        with op.batch_alter_table('groups') as batch_op:
            batch_op.alter_column(
                'min_cash_reserve',
                existing_type=sa.Numeric(14,2),
                existing_nullable=False,
                server_default=None,
            )
    else:
        op.alter_column('groups', 'min_cash_reserve', server_default=None)

def downgrade():
    op.drop_column('groups', 'max_exposure_ratio')
    op.drop_column('groups', 'max_global_exposure')
    op.drop_column('groups', 'max_member_exposure')
    op.drop_column('groups', 'min_cash_reserve')
