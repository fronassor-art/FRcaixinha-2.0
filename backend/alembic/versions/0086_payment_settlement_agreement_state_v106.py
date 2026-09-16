"""add Agreement settlement v5 evidence and state revision"""

from alembic import op
import sqlalchemy as sa


revision = "0086_payment_settlement_agreement_state_v106"
down_revision = "0085_payment_settlement_installment_state_v105"
branch_labels = None
depends_on = None


_RECEIPT_V1_V2_V3_V4_V5 = "receipt_version IN ('v1', 'v2', 'v3', 'v4', 'v5')"
_RECEIPT_V1_V2_V3_V4 = "receipt_version IN ('v1', 'v2', 'v3', 'v4')"
_AGREEMENT_EVIDENCE_COLUMNS = (
    "agreement_installment_status_before",
    "agreement_installment_status_after",
    "agreement_installment_paid_at_before",
    "agreement_installment_paid_at_after",
    "agreement_installment_paid_amount_before",
    "agreement_installment_paid_amount_after",
    "agreement_installment_paid_penalty_amount_before",
    "agreement_installment_paid_penalty_amount_after",
    "collection_agreement_status_before",
    "collection_agreement_status_after",
    "collection_agreement_state_revision_before",
    "collection_agreement_state_revision_after",
)
_V5_EVIDENCE_VERSION = (
    "receipt_version = 'v5' OR ("
    "agreement_installment_status_before IS NULL AND agreement_installment_status_after IS NULL AND "
    "agreement_installment_paid_at_before IS NULL AND agreement_installment_paid_at_after IS NULL AND "
    "agreement_installment_paid_amount_before IS NULL AND agreement_installment_paid_amount_after IS NULL AND "
    "agreement_installment_paid_penalty_amount_before IS NULL AND agreement_installment_paid_penalty_amount_after IS NULL AND "
    "collection_agreement_status_before IS NULL AND collection_agreement_status_after IS NULL AND "
    "collection_agreement_state_revision_before IS NULL AND collection_agreement_state_revision_after IS NULL)"
)
_V5_EVIDENCE_COMPLETE = (
    "receipt_version != 'v5' OR ("
    "agreement_installment_status_before IS NOT NULL AND agreement_installment_status_after IS NOT NULL AND "
    "agreement_installment_paid_amount_before IS NOT NULL AND agreement_installment_paid_amount_after IS NOT NULL AND "
    "agreement_installment_paid_penalty_amount_before IS NOT NULL AND agreement_installment_paid_penalty_amount_after IS NOT NULL AND "
    "collection_agreement_status_before IS NOT NULL AND collection_agreement_status_after IS NOT NULL AND "
    "collection_agreement_state_revision_before IS NOT NULL AND collection_agreement_state_revision_after IS NOT NULL)"
)
_V5_NO_LOAN_EVIDENCE = (
    "receipt_version != 'v5' OR (loan_status_before IS NULL AND loan_status_after IS NULL AND "
    "loan_state_revision_before IS NULL AND loan_state_revision_after IS NULL AND "
    "loan_paid_at_before IS NULL AND loan_paid_at_after IS NULL AND "
    "loan_installment_status_before IS NULL AND loan_installment_status_after IS NULL AND "
    "loan_installment_paid_at_before IS NULL AND loan_installment_paid_at_after IS NULL)"
)
_V5_AMOUNT_NONNEGATIVE = (
    "receipt_version != 'v5' OR (agreement_installment_paid_amount_before >= 0 AND "
    "agreement_installment_paid_amount_after >= 0 AND "
    "agreement_installment_paid_penalty_amount_before >= 0 AND "
    "agreement_installment_paid_penalty_amount_after >= 0)"
)
_V5_INSTALLMENT_STATUSES = (
    "receipt_version != 'v5' OR (agreement_installment_status_before IN ('OPEN', 'PARTIAL', 'PAID') AND "
    "agreement_installment_status_after IN ('OPEN', 'PARTIAL', 'PAID'))"
)
_V5_AGREEMENT_STATUSES = (
    "receipt_version != 'v5' OR (collection_agreement_status_before IN ('APPROVED', 'SETTLED') AND "
    "collection_agreement_status_after IN ('APPROVED', 'SETTLED'))"
)


def _add_columns(batch_op):
    batch_op.add_column(sa.Column("state_revision", sa.Integer(), server_default="0", nullable=False))


def _add_settlement_columns(batch_op):
    batch_op.add_column(sa.Column("agreement_installment_status_before", sa.String(20), nullable=True))
    batch_op.add_column(sa.Column("agreement_installment_status_after", sa.String(20), nullable=True))
    batch_op.add_column(sa.Column("agreement_installment_paid_at_before", sa.DateTime(timezone=True), nullable=True))
    batch_op.add_column(sa.Column("agreement_installment_paid_at_after", sa.DateTime(timezone=True), nullable=True))
    batch_op.add_column(sa.Column("agreement_installment_paid_amount_before", sa.Numeric(14, 2), nullable=True))
    batch_op.add_column(sa.Column("agreement_installment_paid_amount_after", sa.Numeric(14, 2), nullable=True))
    batch_op.add_column(sa.Column("agreement_installment_paid_penalty_amount_before", sa.Numeric(14, 2), nullable=True))
    batch_op.add_column(sa.Column("agreement_installment_paid_penalty_amount_after", sa.Numeric(14, 2), nullable=True))
    batch_op.add_column(sa.Column("collection_agreement_status_before", sa.String(20), nullable=True))
    batch_op.add_column(sa.Column("collection_agreement_status_after", sa.String(20), nullable=True))
    batch_op.add_column(sa.Column("collection_agreement_state_revision_before", sa.Integer(), nullable=True))
    batch_op.add_column(sa.Column("collection_agreement_state_revision_after", sa.Integer(), nullable=True))


def _create_new_settlement_checks(batch_op, *, table_name=None):
    def create(name, expression):
        if table_name is None:
            batch_op.create_check_constraint(name, expression)
        else:
            batch_op.create_check_constraint(name, table_name, expression)

    create("ck_payment_settlements_receipt_version", _RECEIPT_V1_V2_V3_V4_V5)
    create("ck_payment_settlements_v5_agreement_only", "receipt_version != 'v5' OR obligation_type = 'AGREEMENT_INSTALLMENT'")
    create("ck_payment_settlements_agreement_evidence_version", _V5_EVIDENCE_VERSION)
    create("ck_payment_settlements_v5_agreement_evidence_complete", _V5_EVIDENCE_COMPLETE)
    create("ck_payment_settlements_v5_no_loan_evidence", _V5_NO_LOAN_EVIDENCE)
    create("ck_payment_settlements_v5_agreement_revision_order", "receipt_version != 'v5' OR collection_agreement_state_revision_after = collection_agreement_state_revision_before + 1")
    create("ck_payment_settlements_v5_agreement_amounts_nonnegative", _V5_AMOUNT_NONNEGATIVE)
    create("ck_payment_settlements_v5_installment_statuses", _V5_INSTALLMENT_STATUSES)
    create("ck_payment_settlements_v5_agreement_statuses", _V5_AGREEMENT_STATUSES)
    create("ck_payment_settlements_v5_paid_amount_equation", "receipt_version != 'v5' OR agreement_installment_paid_amount_after = agreement_installment_paid_amount_before + principal_applied")
    create("ck_payment_settlements_v5_paid_penalty_equation", "receipt_version != 'v5' OR agreement_installment_paid_penalty_amount_after = agreement_installment_paid_penalty_amount_before + penalty_applied")


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("collection_agreements", recreate="always") as batch_op:
            _add_columns(batch_op)
            batch_op.create_check_constraint("ck_collection_agreements_state_revision_nonnegative", "state_revision >= 0")
        with op.batch_alter_table("payment_settlements", recreate="always") as batch_op:
            _add_settlement_columns(batch_op)
            batch_op.drop_constraint("ck_payment_settlements_receipt_version", type_="check")
            _create_new_settlement_checks(batch_op)
    else:
        op.add_column("collection_agreements", sa.Column("state_revision", sa.Integer(), server_default="0", nullable=False))
        op.create_check_constraint("ck_collection_agreements_state_revision_nonnegative", "collection_agreements", "state_revision >= 0")
        for name, column in (
            ("agreement_installment_status_before", sa.String(20)),
            ("agreement_installment_status_after", sa.String(20)),
            ("agreement_installment_paid_at_before", sa.DateTime(timezone=True)),
            ("agreement_installment_paid_at_after", sa.DateTime(timezone=True)),
            ("agreement_installment_paid_amount_before", sa.Numeric(14, 2)),
            ("agreement_installment_paid_amount_after", sa.Numeric(14, 2)),
            ("agreement_installment_paid_penalty_amount_before", sa.Numeric(14, 2)),
            ("agreement_installment_paid_penalty_amount_after", sa.Numeric(14, 2)),
            ("collection_agreement_status_before", sa.String(20)),
            ("collection_agreement_status_after", sa.String(20)),
            ("collection_agreement_state_revision_before", sa.Integer()),
            ("collection_agreement_state_revision_after", sa.Integer()),
        ):
            op.add_column("payment_settlements", sa.Column(name, column, nullable=True))
        op.drop_constraint("ck_payment_settlements_receipt_version", "payment_settlements", type_="check")
        _create_new_settlement_checks(op, table_name="payment_settlements")


def _preflight(bind):
    evidence = " OR ".join(f"{name} IS NOT NULL" for name in _AGREEMENT_EVIDENCE_COLUMNS)
    settlement_count = bind.execute(sa.text(
        "SELECT COUNT(*) FROM payment_settlements WHERE receipt_version = 'v5' OR (" + evidence + ")"
    )).scalar_one()
    revision_count = bind.execute(sa.text(
        "SELECT COUNT(*) FROM collection_agreements WHERE state_revision <> 0"
    )).scalar_one()
    if int(settlement_count):
        raise RuntimeError("Cannot downgrade 0086: Agreement v5 evidence exists")
    if int(revision_count):
        raise RuntimeError("Cannot downgrade 0086: CollectionAgreement state revision is in use")


def _restore_old_settlement_checks(batch_op):
    batch_op.create_check_constraint("ck_payment_settlements_receipt_version", _RECEIPT_V1_V2_V3_V4)
    batch_op.create_check_constraint("ck_payment_settlements_installment_state_version", "receipt_version = 'v4' OR (loan_installment_status_before IS NULL AND loan_installment_status_after IS NULL AND loan_installment_paid_at_before IS NULL AND loan_installment_paid_at_after IS NULL)")
    batch_op.create_check_constraint("ck_payment_settlements_v4_installment_state_complete", "receipt_version != 'v4' OR (loan_installment_status_before IS NOT NULL AND loan_installment_status_after IS NOT NULL)")


def downgrade():
    bind = op.get_bind()
    _preflight(bind)
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("payment_settlements", recreate="always") as batch_op:
            for name in (
                "ck_payment_settlements_receipt_version",
                "ck_payment_settlements_v5_agreement_only",
                "ck_payment_settlements_agreement_evidence_version",
                "ck_payment_settlements_v5_agreement_evidence_complete",
                "ck_payment_settlements_v5_no_loan_evidence",
                "ck_payment_settlements_v5_agreement_revision_order",
                "ck_payment_settlements_v5_agreement_amounts_nonnegative",
                "ck_payment_settlements_v5_installment_statuses",
                "ck_payment_settlements_v5_agreement_statuses",
                "ck_payment_settlements_v5_paid_amount_equation",
                "ck_payment_settlements_v5_paid_penalty_equation",
            ):
                batch_op.drop_constraint(name, type_="check")
            for column in _AGREEMENT_EVIDENCE_COLUMNS:
                batch_op.drop_column(column)
            _restore_old_settlement_checks(batch_op)
        with op.batch_alter_table("collection_agreements", recreate="always") as batch_op:
            batch_op.drop_constraint("ck_collection_agreements_state_revision_nonnegative", type_="check")
            batch_op.drop_column("state_revision")
    else:
        for name in (
            "ck_payment_settlements_receipt_version",
            "ck_payment_settlements_v5_agreement_only",
            "ck_payment_settlements_agreement_evidence_version",
            "ck_payment_settlements_v5_agreement_evidence_complete",
            "ck_payment_settlements_v5_no_loan_evidence",
            "ck_payment_settlements_v5_agreement_revision_order",
            "ck_payment_settlements_v5_agreement_amounts_nonnegative",
            "ck_payment_settlements_v5_installment_statuses",
            "ck_payment_settlements_v5_agreement_statuses",
            "ck_payment_settlements_v5_paid_amount_equation",
            "ck_payment_settlements_v5_paid_penalty_equation",
        ):
            op.drop_constraint(name, "payment_settlements", type_="check")
        for column in _AGREEMENT_EVIDENCE_COLUMNS:
            op.drop_column("payment_settlements", column)
        op.create_check_constraint("ck_payment_settlements_receipt_version", "payment_settlements", _RECEIPT_V1_V2_V3_V4)
        op.create_check_constraint("ck_payment_settlements_installment_state_version", "payment_settlements", "receipt_version = 'v4' OR (loan_installment_status_before IS NULL AND loan_installment_status_after IS NULL AND loan_installment_paid_at_before IS NULL AND loan_installment_paid_at_after IS NULL)")
        op.create_check_constraint("ck_payment_settlements_v4_installment_state_complete", "payment_settlements", "receipt_version != 'v4' OR (loan_installment_status_before IS NOT NULL AND loan_installment_status_after IS NOT NULL)")
        op.drop_constraint("ck_collection_agreements_state_revision_nonnegative", "collection_agreements", type_="check")
        op.drop_column("collection_agreements", "state_revision")
