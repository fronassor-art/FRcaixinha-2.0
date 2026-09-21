"""persist the calculation version on loans"""

from alembic import op
import sqlalchemy as sa


revision = "0091_loan_calculation_version"
down_revision = "0090_member_financial_contribution_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "loans",
        sa.Column("calculation_version", sa.String(60), nullable=True),
    )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("loans", recreate="always") as batch_op:
            batch_op.drop_column("calculation_version")
    else:
        op.drop_column("loans", "calculation_version")
