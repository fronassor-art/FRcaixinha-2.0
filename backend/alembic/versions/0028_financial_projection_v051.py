from alembic import op
import sqlalchemy as sa
revision='0028_financial_projection_v051'; down_revision='0027_executive_dashboard_v050'; branch_labels=None; depends_on=None

def upgrade():
    op.create_table('financial_projection_snapshots',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('as_of_date',sa.Date(),nullable=False),
        sa.Column('horizon_months',sa.Integer(),nullable=False),
        sa.Column('scenario',sa.String(20),nullable=False),
        sa.Column('status',sa.String(20),nullable=False,server_default='PASS'),
        sa.Column('snapshot_json',sa.Text(),nullable=False),
        sa.Column('snapshot_hash',sa.String(64),nullable=False),
        sa.Column('generated_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_unique_constraint('uq_fin_projection_scope','financial_projection_snapshots',['as_of_date','horizon_months','scenario'])
    op.create_index('ix_fin_projection_as_of_date','financial_projection_snapshots',['as_of_date'])
    op.create_index('ix_fin_projection_scenario','financial_projection_snapshots',['scenario'])
    op.create_index('ix_fin_projection_status','financial_projection_snapshots',['status'])
    op.create_index('ix_fin_projection_hash','financial_projection_snapshots',['snapshot_hash'],unique=True)

def downgrade():
    op.drop_index('ix_fin_projection_hash',table_name='financial_projection_snapshots'); op.drop_index('ix_fin_projection_status',table_name='financial_projection_snapshots'); op.drop_index('ix_fin_projection_scenario',table_name='financial_projection_snapshots'); op.drop_index('ix_fin_projection_as_of_date',table_name='financial_projection_snapshots'); op.drop_constraint('uq_fin_projection_scope','financial_projection_snapshots',type_='unique'); op.drop_table('financial_projection_snapshots')
