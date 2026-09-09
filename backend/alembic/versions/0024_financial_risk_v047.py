from alembic import op
import sqlalchemy as sa
revision='0024_financial_risk_v047'; down_revision='0023_security_advanced_v046'; branch_labels=None; depends_on=None

def upgrade():
    op.create_table('financial_risk_assessments',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('subject_type',sa.String(30),nullable=False),
        sa.Column('subject_id',sa.String(80),nullable=False),
        sa.Column('member_id',sa.Integer(),sa.ForeignKey('members.id'),nullable=False),
        sa.Column('score',sa.Integer(),nullable=False),
        sa.Column('status',sa.String(20),nullable=False),
        sa.Column('reasons',sa.Text(),nullable=False),
        sa.Column('rules_json',sa.Text(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_financial_risk_subject_type','financial_risk_assessments',['subject_type'])
    op.create_index('ix_financial_risk_subject_id','financial_risk_assessments',['subject_id'])
    op.create_index('ix_financial_risk_member','financial_risk_assessments',['member_id'])
    op.create_index('ix_financial_risk_status','financial_risk_assessments',['status'])
    op.create_index('ix_financial_risk_created','financial_risk_assessments',['created_at'])
def downgrade():
    op.drop_index('ix_financial_risk_created',table_name='financial_risk_assessments'); op.drop_index('ix_financial_risk_status',table_name='financial_risk_assessments'); op.drop_index('ix_financial_risk_member',table_name='financial_risk_assessments'); op.drop_index('ix_financial_risk_subject_id',table_name='financial_risk_assessments'); op.drop_index('ix_financial_risk_subject_type',table_name='financial_risk_assessments'); op.drop_table('financial_risk_assessments')
