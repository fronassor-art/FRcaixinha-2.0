from alembic import op
import sqlalchemy as sa
revision='0025_approval_engine_v048'; down_revision='0024_financial_risk_v047'; branch_labels=None; depends_on=None

def upgrade():
    op.add_column('groups', sa.Column('max_loan_amount', sa.Numeric(14,2), nullable=True))
    op.add_column('groups', sa.Column('max_loan_income_multiple', sa.Numeric(8,3), nullable=True))

def downgrade():
    op.drop_column('groups','max_loan_income_multiple')
    op.drop_column('groups','max_loan_amount')
