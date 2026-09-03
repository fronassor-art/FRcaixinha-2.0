from alembic import op
import sqlalchemy as sa
revision='0026_collection_recovery_v049'; down_revision='0025_approval_engine_v048'; branch_labels=None; depends_on=None

def upgrade():
    op.create_table('collection_cases',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('member_id',sa.Integer(),sa.ForeignKey('members.id'),nullable=False),
        sa.Column('loan_id',sa.Integer(),sa.ForeignKey('loans.id'),nullable=True),
        sa.Column('status',sa.String(20),nullable=False,server_default='OPEN'),
        sa.Column('stage',sa.String(30),nullable=False,server_default='SOFT'),
        sa.Column('opened_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('last_action_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('next_action_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('resolved_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('resolved_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
        sa.Column('resolution_note',sa.Text(),nullable=True))
    op.create_index('ix_collection_cases_member','collection_cases',['member_id'])
    op.create_index('ix_collection_cases_loan','collection_cases',['loan_id'])
    op.create_index('ix_collection_cases_status','collection_cases',['status'])
    op.create_index('ix_collection_cases_next_action','collection_cases',['next_action_at'])
    op.create_table('payment_promises',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('case_id',sa.Integer(),sa.ForeignKey('collection_cases.id'),nullable=False),
        sa.Column('member_id',sa.Integer(),sa.ForeignKey('members.id'),nullable=False),
        sa.Column('promised_amount',sa.Numeric(14,2),nullable=False),
        sa.Column('promised_date',sa.Date(),nullable=False),
        sa.Column('status',sa.String(20),nullable=False,server_default='PENDING'),
        sa.Column('created_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('fulfilled_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('note',sa.Text(),nullable=True))
    op.create_index('ix_payment_promises_case','payment_promises',['case_id'])
    op.create_index('ix_payment_promises_member','payment_promises',['member_id'])
    op.create_index('ix_payment_promises_status_date','payment_promises',['status','promised_date'])

def downgrade():
    op.drop_index('ix_payment_promises_status_date',table_name='payment_promises'); op.drop_index('ix_payment_promises_member',table_name='payment_promises'); op.drop_index('ix_payment_promises_case',table_name='payment_promises'); op.drop_table('payment_promises')
    op.drop_index('ix_collection_cases_next_action',table_name='collection_cases'); op.drop_index('ix_collection_cases_status',table_name='collection_cases'); op.drop_index('ix_collection_cases_loan',table_name='collection_cases'); op.drop_index('ix_collection_cases_member',table_name='collection_cases'); op.drop_table('collection_cases')
