"""add Contribution identity to member financial entries"""

from alembic import op
import sqlalchemy as sa


revision = "0090_member_financial_contribution_identity"
down_revision = "0089_collection_agreement_subjects"
branch_labels = None
depends_on = None


INDEX_NAME = "uq_member_financial_entries_one_contribution_settlement_credit"


def _create_indexes():
    op.create_index(
        INDEX_NAME,
        "member_financial_entries",
        ["payment_settlement_id"],
        unique=True,
        sqlite_where=sa.text(
            "payment_settlement_id IS NOT NULL AND "
            "entry_type = 'CONTRIBUTION' AND direction = 'CREDIT'"
        ),
        postgresql_where=sa.text(
            "payment_settlement_id IS NOT NULL AND "
            "entry_type = 'CONTRIBUTION' AND direction = 'CREDIT'"
        ),
    )


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("member_financial_entries", recreate="always") as batch_op:
            batch_op.add_column(sa.Column("contribution_id", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("payment_settlement_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_member_financial_entries_contribution_id",
                "contributions",
                ["contribution_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch_op.create_foreign_key(
                "fk_member_financial_entries_payment_settlement_id",
                "payment_settlements",
                ["payment_settlement_id"],
                ["id"],
                ondelete="RESTRICT",
            )
    else:
        op.add_column(
            "member_financial_entries",
            sa.Column("contribution_id", sa.Integer(), nullable=True),
        )
        op.add_column(
            "member_financial_entries",
            sa.Column("payment_settlement_id", sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            "fk_member_financial_entries_contribution_id",
            "member_financial_entries",
            "contributions",
            ["contribution_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_foreign_key(
            "fk_member_financial_entries_payment_settlement_id",
            "member_financial_entries",
            "payment_settlements",
            ["payment_settlement_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index(
        "ix_member_financial_entries_contribution_id",
        "member_financial_entries",
        ["contribution_id"],
        unique=False,
    )
    op.create_index(
        "ix_member_financial_entries_payment_settlement_id",
        "member_financial_entries",
        ["payment_settlement_id"],
        unique=False,
    )
    _create_indexes()


def downgrade():
    op.drop_index(INDEX_NAME, table_name="member_financial_entries")
    op.drop_index(
        "ix_member_financial_entries_payment_settlement_id",
        table_name="member_financial_entries",
    )
    op.drop_index(
        "ix_member_financial_entries_contribution_id",
        table_name="member_financial_entries",
    )
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("member_financial_entries", recreate="always") as batch_op:
            batch_op.drop_constraint(
                "fk_member_financial_entries_payment_settlement_id",
                type_="foreignkey",
            )
            batch_op.drop_constraint(
                "fk_member_financial_entries_contribution_id",
                type_="foreignkey",
            )
            batch_op.drop_column("payment_settlement_id")
            batch_op.drop_column("contribution_id")
    else:
        op.drop_constraint(
            "fk_member_financial_entries_payment_settlement_id",
            "member_financial_entries",
            type_="foreignkey",
        )
        op.drop_constraint(
            "fk_member_financial_entries_contribution_id",
            "member_financial_entries",
            type_="foreignkey",
        )
        op.drop_column("member_financial_entries", "payment_settlement_id")
        op.drop_column("member_financial_entries", "contribution_id")
