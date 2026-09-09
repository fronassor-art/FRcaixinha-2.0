"""v0.40 advanced financial reconciliation snapshots"""
from alembic import op
import sqlalchemy as sa
revision="0018_financial_reconciliation_v040"
down_revision="0017_agreements_v039"
branch_labels=None
depends_on=None
def upgrade():
    op.create_table("financial_reconciliations",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("competence",sa.Date(),nullable=False),
        sa.Column("status",sa.String(20),nullable=False),
        sa.Column("snapshot_json",sa.Text(),nullable=False),
        sa.Column("snapshot_hash",sa.String(64),nullable=False),
        sa.Column("run_by",sa.Integer(),sa.ForeignKey("users.id"),nullable=True),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint("competence","snapshot_hash",name="uq_fin_recon_comp_hash"))
    op.create_index("ix_financial_reconciliations_competence","financial_reconciliations",["competence"])
    op.create_index("ix_financial_reconciliations_status","financial_reconciliations",["status"])
    op.create_index("ix_financial_reconciliations_snapshot_hash","financial_reconciliations",["snapshot_hash"],unique=True)
def downgrade():
    op.drop_index("ix_financial_reconciliations_snapshot_hash",table_name="financial_reconciliations")
    op.drop_index("ix_financial_reconciliations_status",table_name="financial_reconciliations")
    op.drop_index("ix_financial_reconciliations_competence",table_name="financial_reconciliations")
    op.drop_table("financial_reconciliations")
