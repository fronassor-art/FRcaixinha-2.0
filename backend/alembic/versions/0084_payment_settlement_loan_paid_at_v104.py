"""add paid_at evidence to Loan payment settlements"""

from alembic import op
import sqlalchemy as sa


revision = "0084_payment_settlement_loan_paid_at_v104"
down_revision = "0083_payment_reversal_v104"
branch_labels = None
depends_on = None


_RECEIPT_V1_V2_V3 = "receipt_version IN ('v1', 'v2', 'v3')"
_RECEIPT_V1_V2 = "receipt_version IN ('v1', 'v2')"
_PAID_AT_VERSION = "receipt_version = 'v3' OR (loan_paid_at_before IS NULL AND loan_paid_at_after IS NULL)"
_V3_LOAN_ONLY = "receipt_version != 'v3' OR obligation_type = 'LOAN_INSTALLMENT'"


def _drop_checks(batch_op, *, new_checks=False):
    names = ["ck_payment_settlements_receipt_version"]
    if new_checks:
        names.extend([
            "ck_payment_settlements_paid_at_version",
            "ck_payment_settlements_v3_loan_only",
        ])
    for name in names:
        batch_op.drop_constraint(name, type_="check")


def _create_new_checks(batch_op, receipt_expression):
    batch_op.create_check_constraint("ck_payment_settlements_receipt_version", receipt_expression)
    batch_op.create_check_constraint("ck_payment_settlements_paid_at_version", _PAID_AT_VERSION)
    batch_op.create_check_constraint("ck_payment_settlements_v3_loan_only", _V3_LOAN_ONLY)


def _upgrade_sqlite():
    with op.batch_alter_table("payment_settlements", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("loan_paid_at_before", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("loan_paid_at_after", sa.DateTime(timezone=True), nullable=True))
        _drop_checks(batch_op)
        _create_new_checks(batch_op, _RECEIPT_V1_V2_V3)


def _upgrade_postgresql():
    op.add_column("payment_settlements", sa.Column("loan_paid_at_before", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payment_settlements", sa.Column("loan_paid_at_after", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint("ck_payment_settlements_receipt_version", "payment_settlements", type_="check")
    op.create_check_constraint("ck_payment_settlements_receipt_version", "payment_settlements", _RECEIPT_V1_V2_V3)
    op.create_check_constraint("ck_payment_settlements_paid_at_version", "payment_settlements", _PAID_AT_VERSION)
    op.create_check_constraint("ck_payment_settlements_v3_loan_only", "payment_settlements", _V3_LOAN_ONLY)


def _downgrade_preflight(bind):
    checks = {
        "settlement v3": "SELECT COUNT(*) FROM payment_settlements WHERE receipt_version = 'v3'",
        "Loan paid_at evidence": (
            "SELECT COUNT(*) FROM payment_settlements "
            "WHERE loan_paid_at_before IS NOT NULL OR loan_paid_at_after IS NOT NULL"
        ),
    }
    for label, query in checks.items():
        if int(bind.execute(sa.text(query)).scalar_one()):
            raise RuntimeError(f"Cannot downgrade 0084: {label} exists")


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _upgrade_sqlite()
    else:
        _upgrade_postgresql()


def _downgrade_sqlite():
    with op.batch_alter_table("payment_settlements", recreate="always") as batch_op:
        _drop_checks(batch_op, new_checks=True)
        batch_op.drop_column("loan_paid_at_before")
        batch_op.drop_column("loan_paid_at_after")
        batch_op.create_check_constraint("ck_payment_settlements_receipt_version", _RECEIPT_V1_V2)


def _downgrade_postgresql():
    for name in (
        "ck_payment_settlements_receipt_version",
        "ck_payment_settlements_paid_at_version",
        "ck_payment_settlements_v3_loan_only",
    ):
        op.drop_constraint(name, "payment_settlements", type_="check")
    op.drop_column("payment_settlements", "loan_paid_at_before")
    op.drop_column("payment_settlements", "loan_paid_at_after")
    op.create_check_constraint("ck_payment_settlements_receipt_version", "payment_settlements", _RECEIPT_V1_V2)


def downgrade():
    bind = op.get_bind()
    _downgrade_preflight(bind)
    if bind.dialect.name == "sqlite":
        _downgrade_sqlite()
    else:
        _downgrade_postgresql()
