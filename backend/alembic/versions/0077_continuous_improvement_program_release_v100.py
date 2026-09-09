from alembic import op
import sqlalchemy as sa
revision='0077'; down_revision='0076'; branch_labels=None; depends_on=None
def upgrade():
    op.create_table('continuous_improvement_program_release_snapshots', sa.Column('id',sa.Integer(),primary_key=True), sa.Column('release_version',sa.String(20),nullable=False), sa.Column('status',sa.String(20),nullable=False), sa.Column('snapshot_json',sa.Text(),nullable=False), sa.Column('snapshot_hash',sa.String(64),nullable=False,unique=True), sa.Column('generated_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=True), sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_0077_status','continuous_improvement_program_release_snapshots',['status'])
    op.create_index('ix_0077_version','continuous_improvement_program_release_snapshots',['release_version'])
    op.create_index('ix_0077_hash','continuous_improvement_program_release_snapshots',['snapshot_hash'],unique=True)
def downgrade():
    op.drop_index('ix_0077_hash',table_name='continuous_improvement_program_release_snapshots')
    op.drop_index('ix_0077_status',table_name='continuous_improvement_program_release_snapshots')
    op.drop_index('ix_0077_version',table_name='continuous_improvement_program_release_snapshots')
    op.drop_table('continuous_improvement_program_release_snapshots')
