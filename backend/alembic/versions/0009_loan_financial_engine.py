"""v0.17 loan financial engine fields"""
from alembic import op
import sqlalchemy as sa

revision = '0009_loan_financial_engine'
down_revision = '0008_loan_installment_pix'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('loans', sa.Column('disbursed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('loans', sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('loan_installments', sa.Column('penalty_amount', sa.Numeric(14,2), nullable=False, server_default='0.00'))
    op.add_column('loan_installments', sa.Column('last_penalty_date', sa.Date(), nullable=True))

def downgrade():
    op.drop_column('loan_installments', 'last_penalty_date')
    op.drop_column('loan_installments', 'penalty_amount')
    op.drop_column('loans', 'paid_at')
    op.drop_column('loans', 'disbursed_at')
