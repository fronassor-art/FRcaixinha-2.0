"""add literal LoanInstallment state evidence to payment settlements"""

from alembic import op
import sqlalchemy as sa


revision = "0085_payment_settlement_installment_state_v105"
down_revision = "0084_payment_settlement_loan_paid_at_v104"
branch_labels = None
depends_on = None


_RECEIPT_V1_V2_V3_V4 = "receipt_version IN ('v1', 'v2', 'v3', 'v4')"
_RECEIPT_V1_V2_V3 = "receipt_version IN ('v1', 'v2', 'v3')"
_PAID_AT_VERSION = "receipt_version IN ('v3', 'v4') OR (loan_paid_at_before IS NULL AND loan_paid_at_after IS NULL)"
_V3_V4_LOAN_ONLY = "receipt_version NOT IN ('v3', 'v4') OR obligation_type = 'LOAN_INSTALLMENT'"
_INSTALLMENT_STATE_VERSION = (
    "receipt_version = 'v4' OR "
    "(loan_installment_status_before IS NULL AND "
    "loan_installment_status_after IS NULL AND "
    "loan_installment_paid_at_before IS NULL AND "
    "loan_installment_paid_at_after IS NULL)"
)
_V4_INSTALLMENT_STATE_COMPLETE = (
    "receipt_version != 'v4' OR "
    "(loan_installment_status_before IS NOT NULL AND "
    "loan_installment_status_after IS NOT NULL)"
)


def _drop_checks(batch_op, *, include_installment=False):
    names = [
        "ck_payment_settlements_receipt_version",
        "ck_payment_settlements_paid_at_version",
        "ck_payment_settlements_v3_loan_only",
    ]
    if include_installment:
        names.extend([
            "ck_payment_settlements_installment_state_version",
            "ck_payment_settlements_v4_installment_state_complete",
        ])
    for name in names:
        batch_op.drop_constraint(name, type_="check")


def _create_checks(batch_op):
    batch_op.create_check_constraint(
        "ck_payment_settlements_receipt_version", _RECEIPT_V1_V2_V3_V4
    )
    batch_op.create_check_constraint(
        "ck_payment_settlements_paid_at_version", _PAID_AT_VERSION
    )
    batch_op.create_check_constraint(
        "ck_payment_settlements_v3_loan_only", _V3_V4_LOAN_ONLY
    )
    batch_op.create_check_constraint(
        "ck_payment_settlements_installment_state_version",
        _INSTALLMENT_STATE_VERSION,
    )
    batch_op.create_check_constraint(
        "ck_payment_settlements_v4_installment_state_complete",
        _V4_INSTALLMENT_STATE_COMPLETE,
    )


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("payment_settlements", recreate="always") as batch_op:
            batch_op.add_column(sa.Column("loan_installment_status_before", sa.String(20), nullable=True))
            batch_op.add_column(sa.Column("loan_installment_status_after", sa.String(20), nullable=True))
            batch_op.add_column(sa.Column("loan_installment_paid_at_before", sa.DateTime(timezone=True), nullable=True))
            batch_op.add_column(sa.Column("loan_installment_paid_at_after", sa.DateTime(timezone=True), nullable=True))
            _drop_checks(batch_op)
            _create_checks(batch_op)
    else:
        op.add_column("payment_settlements", sa.Column("loan_installment_status_before", sa.String(20), nullable=True))
        op.add_column("payment_settlements", sa.Column("loan_installment_status_after", sa.String(20), nullable=True))
        op.add_column("payment_settlements", sa.Column("loan_installment_paid_at_before", sa.DateTime(timezone=True), nullable=True))
        op.add_column("payment_settlements", sa.Column("loan_installment_paid_at_after", sa.DateTime(timezone=True), nullable=True))
        for name in (
            "ck_payment_settlements_receipt_version",
            "ck_payment_settlements_paid_at_version",
            "ck_payment_settlements_v3_loan_only",
        ):
            op.drop_constraint(name, "payment_settlements", type_="check")
        op.create_check_constraint("ck_payment_settlements_receipt_version", "payment_settlements", _RECEIPT_V1_V2_V3_V4)
        op.create_check_constraint("ck_payment_settlements_paid_at_version", "payment_settlements", _PAID_AT_VERSION)
        op.create_check_constraint("ck_payment_settlements_v3_loan_only", "payment_settlements", _V3_V4_LOAN_ONLY)
        op.create_check_constraint("ck_payment_settlements_installment_state_version", "payment_settlements", _INSTALLMENT_STATE_VERSION)
        op.create_check_constraint("ck_payment_settlements_v4_installment_state_complete", "payment_settlements", _V4_INSTALLMENT_STATE_COMPLETE)


def _downgrade_preflight(bind):
    count = bind.execute(sa.text(
        "SELECT COUNT(*) FROM payment_settlements "
        "WHERE receipt_version = 'v4' OR "
        "loan_installment_status_before IS NOT NULL OR "
        "loan_installment_status_after IS NOT NULL OR "
        "loan_installment_paid_at_before IS NOT NULL OR "
        "loan_installment_paid_at_after IS NOT NULL"
    )).scalar_one()
    if int(count):
        raise RuntimeError("Cannot downgrade 0085: v4 installment evidence exists")


def downgrade():
    bind = op.get_bind()
    _downgrade_preflight(bind)
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("payment_settlements", recreate="always") as batch_op:
            _drop_checks(batch_op, include_installment=True)
            batch_op.drop_column("loan_installment_status_before")
            batch_op.drop_column("loan_installment_status_after")
            batch_op.drop_column("loan_installment_paid_at_before")
            batch_op.drop_column("loan_installment_paid_at_after")
            batch_op.create_check_constraint("ck_payment_settlements_receipt_version", _RECEIPT_V1_V2_V3)
            batch_op.create_check_constraint("ck_payment_settlements_paid_at_version", "receipt_version = 'v3' OR (loan_paid_at_before IS NULL AND loan_paid_at_after IS NULL)")
            batch_op.create_check_constraint("ck_payment_settlements_v3_loan_only", "receipt_version != 'v3' OR obligation_type = 'LOAN_INSTALLMENT'")
    else:
        for name in (
            "ck_payment_settlements_receipt_version",
            "ck_payment_settlements_paid_at_version",
            "ck_payment_settlements_v3_loan_only",
            "ck_payment_settlements_installment_state_version",
            "ck_payment_settlements_v4_installment_state_complete",
        ):
            op.drop_constraint(name, "payment_settlements", type_="check")
        op.drop_column("payment_settlements", "loan_installment_status_before")
        op.drop_column("payment_settlements", "loan_installment_status_after")
        op.drop_column("payment_settlements", "loan_installment_paid_at_before")
        op.drop_column("payment_settlements", "loan_installment_paid_at_after")
        op.create_check_constraint("ck_payment_settlements_receipt_version", "payment_settlements", _RECEIPT_V1_V2_V3)
        op.create_check_constraint("ck_payment_settlements_paid_at_version", "payment_settlements", "receipt_version = 'v3' OR (loan_paid_at_before IS NULL AND loan_paid_at_after IS NULL)")
        op.create_check_constraint("ck_payment_settlements_v3_loan_only", "payment_settlements", "receipt_version != 'v3' OR obligation_type = 'LOAN_INSTALLMENT'")
