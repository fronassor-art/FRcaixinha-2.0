"""allow AgreementInstallment payment settlements"""

from alembic import op
import sqlalchemy as sa


revision = "0082_payment_settlement_agreement_v104"
down_revision = "0081_master_integrity_v104"
branch_labels = None
depends_on = None


_SINGLE_OBLIGATION = (
    "(obligation_type = 'CONTRIBUTION' AND contribution_id IS NOT NULL "
    "AND loan_installment_id IS NULL AND agreement_installment_id IS NULL) "
    "OR (obligation_type = 'LOAN_INSTALLMENT' AND contribution_id IS NULL "
    "AND loan_installment_id IS NOT NULL AND agreement_installment_id IS NULL) "
    "OR (obligation_type = 'AGREEMENT_INSTALLMENT' AND contribution_id IS NULL "
    "AND loan_installment_id IS NULL AND agreement_installment_id IS NOT NULL)"
)
_STATUS_BEFORE = "obligation_status_before IN ('OPEN', 'PENDING', 'PARTIAL', 'OVERDUE', 'PAID')"
_STATUS_AFTER = "obligation_status_after IN ('OPEN', 'PENDING', 'PARTIAL', 'OVERDUE', 'PAID')"


def _replace_constraints(batch_op):
    batch_op.drop_constraint("ck_payment_settlements_single_obligation", type_="check")
    batch_op.drop_constraint("ck_payment_settlements_status_before", type_="check")
    batch_op.drop_constraint("ck_payment_settlements_status_after", type_="check")
    batch_op.create_check_constraint("ck_payment_settlements_single_obligation", _SINGLE_OBLIGATION)
    batch_op.create_check_constraint("ck_payment_settlements_status_before", _STATUS_BEFORE)
    batch_op.create_check_constraint("ck_payment_settlements_status_after", _STATUS_AFTER)
    batch_op.create_check_constraint(
        "ck_payment_settlements_agreement_no_interest",
        "obligation_type != 'AGREEMENT_INSTALLMENT' OR interest_applied = 0",
    )


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("payment_settlements", recreate="always") as batch_op:
            batch_op.add_column(sa.Column("agreement_installment_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_payment_settlements_agreement_installment_id",
                "agreement_installments",
                ["agreement_installment_id"],
                ["id"],
            )
            _replace_constraints(batch_op)
    else:
        op.add_column("payment_settlements", sa.Column("agreement_installment_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_payment_settlements_agreement_installment_id",
            "payment_settlements",
            "agreement_installments",
            ["agreement_installment_id"],
            ["id"],
        )
        op.drop_constraint("ck_payment_settlements_single_obligation", "payment_settlements", type_="check")
        op.drop_constraint("ck_payment_settlements_status_before", "payment_settlements", type_="check")
        op.drop_constraint("ck_payment_settlements_status_after", "payment_settlements", type_="check")
        op.create_check_constraint("ck_payment_settlements_single_obligation", "payment_settlements", _SINGLE_OBLIGATION)
        op.create_check_constraint("ck_payment_settlements_status_before", "payment_settlements", _STATUS_BEFORE)
        op.create_check_constraint("ck_payment_settlements_status_after", "payment_settlements", _STATUS_AFTER)
        op.create_check_constraint(
            "ck_payment_settlements_agreement_no_interest",
            "payment_settlements",
            "obligation_type != 'AGREEMENT_INSTALLMENT' OR interest_applied = 0",
        )
    op.create_index(
        "ix_payment_settlements_agreement_installment_id",
        "payment_settlements",
        ["agreement_installment_id"],
    )


def downgrade():
    bind = op.get_bind()
    existing = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM payment_settlements "
            "WHERE obligation_type = 'AGREEMENT_INSTALLMENT'"
        )
    ).scalar_one()
    if existing:
        raise RuntimeError(
            "Cannot downgrade 0082: payment_settlements contains "
            "AGREEMENT_INSTALLMENT rows"
        )

    op.drop_index("ix_payment_settlements_agreement_installment_id", table_name="payment_settlements")
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("payment_settlements", recreate="always") as batch_op:
            batch_op.drop_constraint("ck_payment_settlements_agreement_no_interest", type_="check")
            batch_op.drop_constraint("ck_payment_settlements_single_obligation", type_="check")
            batch_op.drop_constraint("ck_payment_settlements_status_before", type_="check")
            batch_op.drop_constraint("ck_payment_settlements_status_after", type_="check")
            batch_op.drop_constraint("fk_payment_settlements_agreement_installment_id", type_="foreignkey")
            batch_op.drop_column("agreement_installment_id")
            batch_op.create_check_constraint(
                "ck_payment_settlements_single_obligation",
                "(obligation_type = 'CONTRIBUTION' AND contribution_id IS NOT NULL AND loan_installment_id IS NULL) "
                "OR (obligation_type = 'LOAN_INSTALLMENT' AND loan_installment_id IS NOT NULL AND contribution_id IS NULL)",
            )
            batch_op.create_check_constraint(
                "ck_payment_settlements_status_before",
                "obligation_status_before IN ('PENDING', 'PARTIAL', 'OVERDUE', 'PAID')",
            )
            batch_op.create_check_constraint(
                "ck_payment_settlements_status_after",
                "obligation_status_after IN ('PENDING', 'PARTIAL', 'OVERDUE', 'PAID')",
            )
    else:
        op.drop_constraint("ck_payment_settlements_agreement_no_interest", "payment_settlements", type_="check")
        op.drop_constraint("ck_payment_settlements_single_obligation", "payment_settlements", type_="check")
        op.drop_constraint("ck_payment_settlements_status_before", "payment_settlements", type_="check")
        op.drop_constraint("ck_payment_settlements_status_after", "payment_settlements", type_="check")
        op.drop_constraint("fk_payment_settlements_agreement_installment_id", "payment_settlements", type_="foreignkey")
        op.drop_column("payment_settlements", "agreement_installment_id")
        op.create_check_constraint(
            "ck_payment_settlements_single_obligation",
            "payment_settlements",
            "(obligation_type = 'CONTRIBUTION' AND contribution_id IS NOT NULL AND loan_installment_id IS NULL) "
            "OR (obligation_type = 'LOAN_INSTALLMENT' AND loan_installment_id IS NOT NULL AND contribution_id IS NULL)",
        )
        op.create_check_constraint(
            "ck_payment_settlements_status_before",
            "payment_settlements",
            "obligation_status_before IN ('PENDING', 'PARTIAL', 'OVERDUE', 'PAID')",
        )
        op.create_check_constraint(
            "ck_payment_settlements_status_after",
            "payment_settlements",
            "obligation_status_after IN ('PENDING', 'PARTIAL', 'OVERDUE', 'PAID')",
        )
