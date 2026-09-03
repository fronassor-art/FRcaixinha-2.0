from alembic import op
import sqlalchemy as sa
revision='0036_secure_release_v059'
down_revision='0035_integrated_governance_v058'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('secure_release_authorizations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('loan_id', sa.Integer(), sa.ForeignKey('loans.id'), nullable=False),
        sa.Column('group_id', sa.Integer(), sa.ForeignKey('groups.id'), nullable=False),
        sa.Column('governance_hash', sa.String(64), nullable=False),
        sa.Column('governance_snapshot', sa.Text(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('authorized_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('authorized_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('confirmed_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('confirmation_count', sa.Integer(), nullable=False),
        sa.Column('executed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('execution_hash', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    for n,c in [('loan_id','loan_id'),('group_id','group_id'),('governance_hash','governance_hash'),('status','status'),('expires_at','expires_at'),('created_at','created_at')]:
        op.create_index('ix_secure_release_authorizations_'+n, 'secure_release_authorizations', [c])
    op.create_index('ix_secure_release_authorizations_execution_hash','secure_release_authorizations',['execution_hash'],unique=True)

def downgrade():
    for n in ['ix_secure_release_authorizations_execution_hash','ix_secure_release_authorizations_created_at','ix_secure_release_authorizations_expires_at','ix_secure_release_authorizations_status','ix_secure_release_authorizations_governance_hash','ix_secure_release_authorizations_group_id','ix_secure_release_authorizations_loan_id']:
        op.drop_index(n, table_name='secure_release_authorizations')
    op.drop_table('secure_release_authorizations')
