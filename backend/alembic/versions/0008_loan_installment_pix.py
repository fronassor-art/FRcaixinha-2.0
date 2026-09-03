"""v0.16 loan installment Pix payments"""
from alembic import op
import sqlalchemy as sa

revision = '0008_loan_installment_pix'
down_revision = '0007_notifications'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('payments', sa.Column('reference_type', sa.String(length=50), nullable=True))
    op.add_column('payments', sa.Column('reference_id', sa.String(length=80), nullable=True))
    op.create_index('ix_payments_reference_type', 'payments', ['reference_type'])
    op.create_index('ix_payments_reference_id', 'payments', ['reference_id'])

def downgrade():
    op.drop_index('ix_payments_reference_id', table_name='payments')
    op.drop_index('ix_payments_reference_type', table_name='payments')
    op.drop_column('payments', 'reference_id')
    op.drop_column('payments', 'reference_type')
