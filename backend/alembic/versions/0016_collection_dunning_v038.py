"""collection dunning v0.38"""
from alembic import op
import sqlalchemy as sa
revision='0016_collection_dunning_v038'
down_revision='0015_credit_policy_v037'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('loan_installments', sa.Column('collection_stage', sa.String(20), nullable=False, server_default='NORMAL'))
    op.add_column('loan_installments', sa.Column('last_collection_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('loan_installments', sa.Column('collection_attempts', sa.Integer(), nullable=False, server_default='0'))
    op.create_index('ix_installments_collection_stage', 'loan_installments', ['collection_stage'])
    op.create_table(
        'collection_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('installment_id', sa.Integer(), sa.ForeignKey('loan_installments.id'), nullable=False),
        sa.Column('member_id', sa.Integer(), sa.ForeignKey('members.id'), nullable=False),
        sa.Column('event_type', sa.String(50), nullable=False),
        sa.Column('event_date', sa.Date(), nullable=False),
        sa.Column('channel', sa.String(20), nullable=False, server_default='IN_APP'),
        sa.Column('notification_id', sa.Integer(), sa.ForeignKey('notifications.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('installment_id','event_type','event_date', name='uq_collection_event_day'),
        sa.Index('ix_collection_events_member_date','member_id','event_date'),
    )
    op.alter_column('loan_installments','collection_stage',server_default=None)
    op.alter_column('loan_installments','collection_attempts',server_default=None)

def downgrade():
    op.drop_table('collection_events')
    op.drop_index('ix_installments_collection_stage', table_name='loan_installments')
    op.drop_column('loan_installments','collection_attempts')
    op.drop_column('loan_installments','last_collection_at')
    op.drop_column('loan_installments','collection_stage')
