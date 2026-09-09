from alembic import op
import sqlalchemy as sa
revision='0029_scenario_simulation_v052'; down_revision='0028_financial_projection_v051'; branch_labels=None; depends_on=None

def upgrade():
    op.create_table('scenario_simulation_snapshots',
      sa.Column('id',sa.Integer(),primary_key=True), sa.Column('as_of_date',sa.Date(),nullable=False),
      sa.Column('horizon_months',sa.Integer(),nullable=False), sa.Column('scenario',sa.String(20),nullable=False),
      sa.Column('status',sa.String(20),nullable=False), sa.Column('snapshot_json',sa.Text(),nullable=False),
      sa.Column('snapshot_hash',sa.String(64),nullable=False), sa.Column('generated_by',sa.Integer(),sa.ForeignKey('users.id')),
      sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_scenario_simulation_as_of_date','scenario_simulation_snapshots',['as_of_date'])
    op.create_index('ix_scenario_simulation_scenario','scenario_simulation_snapshots',['scenario'])
    op.create_index('ix_scenario_simulation_status','scenario_simulation_snapshots',['status'])
    op.create_index('ix_scenario_simulation_hash','scenario_simulation_snapshots',['snapshot_hash'],unique=True)

def downgrade():
    op.drop_index('ix_scenario_simulation_hash',table_name='scenario_simulation_snapshots'); op.drop_index('ix_scenario_simulation_status',table_name='scenario_simulation_snapshots'); op.drop_index('ix_scenario_simulation_scenario',table_name='scenario_simulation_snapshots'); op.drop_index('ix_scenario_simulation_as_of_date',table_name='scenario_simulation_snapshots'); op.drop_table('scenario_simulation_snapshots')
