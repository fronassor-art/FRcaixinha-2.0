"""add structural PaymentReversal evidence tables and loan state evidence"""

from alembic import op
import sqlalchemy as sa


revision = "0083_payment_reversal_v104"
down_revision = "0082_payment_settlement_agreement_v104"
branch_labels = None
depends_on = None


_LOAN_STATUSES = (
    "'REQUESTED', 'APPROVED', 'REJECTED', 'ACTIVE', "
    "'OVERDUE', 'IN_COLLECTION', 'PAID', 'RESTRUCTURED'"
)
_LOAN_STATUS_BEFORE = f"loan_status_before IS NULL OR loan_status_before IN ({_LOAN_STATUSES})"
_LOAN_STATUS_AFTER = f"loan_status_after IS NULL OR loan_status_after IN ({_LOAN_STATUSES})"
_LOAN_EVIDENCE_COMPLETE = (
    "(loan_status_before IS NULL AND loan_status_after IS NULL "
    "AND loan_state_revision_before IS NULL AND loan_state_revision_after IS NULL) "
    "OR (loan_status_before IS NOT NULL AND loan_status_after IS NOT NULL "
    "AND loan_state_revision_before IS NOT NULL AND loan_state_revision_after IS NOT NULL)"
)
_RECEIPT_V1 = "receipt_version = 'v1'"
_RECEIPT_V1_V2 = "receipt_version IN ('v1', 'v2')"
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


def _settlement_checks(batch_op, *, receipt_expression, include_loan_evidence=True):
    batch_op.create_check_constraint(
        "ck_payment_settlements_nonnegative_amounts",
        "amount_received >= 0 AND amount_applied >= 0 AND principal_applied >= 0 "
        "AND interest_applied >= 0 AND penalty_applied >= 0 AND excess_amount >= 0",
    )
    batch_op.create_check_constraint(
        "ck_payment_settlements_received_allocation",
        "amount_received = amount_applied + excess_amount",
    )
    batch_op.create_check_constraint(
        "ck_payment_settlements_applied_components",
        "amount_applied = principal_applied + interest_applied + penalty_applied",
    )
    batch_op.create_check_constraint("ck_payment_settlements_single_obligation", _SINGLE_OBLIGATION)
    batch_op.create_check_constraint("ck_payment_settlements_status_before", _STATUS_BEFORE)
    batch_op.create_check_constraint("ck_payment_settlements_status_after", _STATUS_AFTER)
    batch_op.create_check_constraint(
        "ck_payment_settlements_agreement_no_interest",
        "obligation_type != 'AGREEMENT_INSTALLMENT' OR interest_applied = 0",
    )
    batch_op.create_check_constraint("ck_payment_settlements_receipt_version", receipt_expression)
    if include_loan_evidence:
        batch_op.create_check_constraint("ck_payment_settlements_loan_evidence_complete", _LOAN_EVIDENCE_COMPLETE)
        batch_op.create_check_constraint("ck_payment_settlements_loan_status_before", _LOAN_STATUS_BEFORE)
        batch_op.create_check_constraint("ck_payment_settlements_loan_status_after", _LOAN_STATUS_AFTER)
        batch_op.create_check_constraint(
            "ck_payment_settlements_loan_revision_before",
            "loan_state_revision_before IS NULL OR loan_state_revision_before >= 0",
        )
        batch_op.create_check_constraint(
            "ck_payment_settlements_loan_revision_after",
            "loan_state_revision_after IS NULL OR loan_state_revision_after >= 0",
        )
        batch_op.create_check_constraint(
            "ck_payment_settlements_loan_revision_order",
            "loan_state_revision_before IS NULL OR loan_state_revision_after > loan_state_revision_before",
        )


def _drop_settlement_checks(batch_op, *, include_loan_evidence=False):
    names = [
        "ck_payment_settlements_nonnegative_amounts",
        "ck_payment_settlements_received_allocation",
        "ck_payment_settlements_applied_components",
        "ck_payment_settlements_single_obligation",
        "ck_payment_settlements_status_before",
        "ck_payment_settlements_status_after",
        "ck_payment_settlements_agreement_no_interest",
        "ck_payment_settlements_receipt_version",
    ]
    if include_loan_evidence:
        names.extend([
            "ck_payment_settlements_loan_evidence_complete",
            "ck_payment_settlements_loan_status_before",
            "ck_payment_settlements_loan_status_after",
            "ck_payment_settlements_loan_revision_before",
            "ck_payment_settlements_loan_revision_after",
            "ck_payment_settlements_loan_revision_order",
        ])
    for name in names:
        batch_op.drop_constraint(name, type_="check")


def _alter_settlements_for_upgrade(bind):
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("payment_settlements", recreate="always") as batch_op:
            batch_op.add_column(sa.Column("loan_status_before", sa.String(30), nullable=True))
            batch_op.add_column(sa.Column("loan_status_after", sa.String(30), nullable=True))
            batch_op.add_column(sa.Column("loan_state_revision_before", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("loan_state_revision_after", sa.Integer(), nullable=True))
            _drop_settlement_checks(batch_op)
            _settlement_checks(batch_op, receipt_expression=_RECEIPT_V1_V2)
    else:
        for name, column in (
            ("loan_status_before", sa.String(30)),
            ("loan_status_after", sa.String(30)),
            ("loan_state_revision_before", sa.Integer()),
            ("loan_state_revision_after", sa.Integer()),
        ):
            op.add_column("payment_settlements", sa.Column(name, column, nullable=True))
        for name in (
            "ck_payment_settlements_nonnegative_amounts",
            "ck_payment_settlements_received_allocation",
            "ck_payment_settlements_applied_components",
            "ck_payment_settlements_single_obligation",
            "ck_payment_settlements_status_before",
            "ck_payment_settlements_status_after",
            "ck_payment_settlements_agreement_no_interest",
            "ck_payment_settlements_receipt_version",
        ):
            op.drop_constraint(name, "payment_settlements", type_="check")
        op.create_check_constraint("ck_payment_settlements_nonnegative_amounts", "payment_settlements", "amount_received >= 0 AND amount_applied >= 0 AND principal_applied >= 0 AND interest_applied >= 0 AND penalty_applied >= 0 AND excess_amount >= 0")
        op.create_check_constraint("ck_payment_settlements_received_allocation", "payment_settlements", "amount_received = amount_applied + excess_amount")
        op.create_check_constraint("ck_payment_settlements_applied_components", "payment_settlements", "amount_applied = principal_applied + interest_applied + penalty_applied")
        op.create_check_constraint("ck_payment_settlements_single_obligation", "payment_settlements", _SINGLE_OBLIGATION)
        op.create_check_constraint("ck_payment_settlements_status_before", "payment_settlements", _STATUS_BEFORE)
        op.create_check_constraint("ck_payment_settlements_status_after", "payment_settlements", _STATUS_AFTER)
        op.create_check_constraint("ck_payment_settlements_agreement_no_interest", "payment_settlements", "obligation_type != 'AGREEMENT_INSTALLMENT' OR interest_applied = 0")
        op.create_check_constraint("ck_payment_settlements_receipt_version", "payment_settlements", _RECEIPT_V1_V2)
        op.create_check_constraint("ck_payment_settlements_loan_evidence_complete", "payment_settlements", _LOAN_EVIDENCE_COMPLETE)
        op.create_check_constraint("ck_payment_settlements_loan_status_before", "payment_settlements", _LOAN_STATUS_BEFORE)
        op.create_check_constraint("ck_payment_settlements_loan_status_after", "payment_settlements", _LOAN_STATUS_AFTER)
        op.create_check_constraint("ck_payment_settlements_loan_revision_before", "payment_settlements", "loan_state_revision_before IS NULL OR loan_state_revision_before >= 0")
        op.create_check_constraint("ck_payment_settlements_loan_revision_after", "payment_settlements", "loan_state_revision_after IS NULL OR loan_state_revision_after >= 0")
        op.create_check_constraint("ck_payment_settlements_loan_revision_order", "payment_settlements", "loan_state_revision_before IS NULL OR loan_state_revision_after > loan_state_revision_before")


def _alter_settlements_for_downgrade(bind):
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("payment_settlements", recreate="always") as batch_op:
            _drop_settlement_checks(batch_op, include_loan_evidence=True)
            for column in ("loan_status_before", "loan_status_after", "loan_state_revision_before", "loan_state_revision_after"):
                batch_op.drop_column(column)
            _settlement_checks(batch_op, receipt_expression=_RECEIPT_V1, include_loan_evidence=False)
    else:
        for name in (
            "ck_payment_settlements_nonnegative_amounts",
            "ck_payment_settlements_received_allocation",
            "ck_payment_settlements_applied_components",
            "ck_payment_settlements_single_obligation",
            "ck_payment_settlements_status_before",
            "ck_payment_settlements_status_after",
            "ck_payment_settlements_agreement_no_interest",
            "ck_payment_settlements_receipt_version",
            "ck_payment_settlements_loan_evidence_complete",
            "ck_payment_settlements_loan_status_before",
            "ck_payment_settlements_loan_status_after",
            "ck_payment_settlements_loan_revision_before",
            "ck_payment_settlements_loan_revision_after",
            "ck_payment_settlements_loan_revision_order",
        ):
            op.drop_constraint(name, "payment_settlements", type_="check")
        for column in ("loan_status_before", "loan_status_after", "loan_state_revision_before", "loan_state_revision_after"):
            op.drop_column("payment_settlements", column)
        op.create_check_constraint("ck_payment_settlements_nonnegative_amounts", "payment_settlements", "amount_received >= 0 AND amount_applied >= 0 AND principal_applied >= 0 AND interest_applied >= 0 AND penalty_applied >= 0 AND excess_amount >= 0")
        op.create_check_constraint("ck_payment_settlements_received_allocation", "payment_settlements", "amount_received = amount_applied + excess_amount")
        op.create_check_constraint("ck_payment_settlements_applied_components", "payment_settlements", "amount_applied = principal_applied + interest_applied + penalty_applied")
        op.create_check_constraint("ck_payment_settlements_single_obligation", "payment_settlements", _SINGLE_OBLIGATION)
        op.create_check_constraint("ck_payment_settlements_status_before", "payment_settlements", _STATUS_BEFORE)
        op.create_check_constraint("ck_payment_settlements_status_after", "payment_settlements", _STATUS_AFTER)
        op.create_check_constraint("ck_payment_settlements_agreement_no_interest", "payment_settlements", "obligation_type != 'AGREEMENT_INSTALLMENT' OR interest_applied = 0")
        op.create_check_constraint("ck_payment_settlements_receipt_version", "payment_settlements", _RECEIPT_V1)


def _count(bind, query):
    return int(bind.execute(sa.text(query)).scalar_one())


def _downgrade_preflight(bind):
    checks = {
        "payment_reversals": "SELECT COUNT(*) FROM payment_reversals",
        "payment_reversal_components": "SELECT COUNT(*) FROM payment_reversal_components",
        "linked MFE": "SELECT COUNT(*) FROM member_financial_entries WHERE payment_reversal_id IS NOT NULL",
        "settlement v2": "SELECT COUNT(*) FROM payment_settlements WHERE receipt_version = 'v2'",
        "settlement loan evidence": "SELECT COUNT(*) FROM payment_settlements WHERE loan_status_before IS NOT NULL OR loan_status_after IS NOT NULL OR loan_state_revision_before IS NOT NULL OR loan_state_revision_after IS NOT NULL",
        "loan state revision": "SELECT COUNT(*) FROM loans WHERE state_revision > 0",
    }
    for label, query in checks.items():
        if _count(bind, query):
            raise RuntimeError(f"Cannot downgrade 0083: {label} exists")


def upgrade():
    op.create_table(
        "payment_reversals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("settlement_id", sa.Integer(), nullable=False),
        sa.Column("admin_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reversal_competence", sa.Date(), nullable=False),
        sa.Column("original_competence", sa.Date(), nullable=True),
        sa.Column("original_due_date", sa.Date(), nullable=True),
        sa.Column("original_date_kind", sa.String(40), nullable=False),
        sa.Column("amount_received", sa.Numeric(14, 2), nullable=False),
        sa.Column("amount_applied", sa.Numeric(14, 2), nullable=False),
        sa.Column("principal_applied", sa.Numeric(14, 2), nullable=False),
        sa.Column("interest_applied", sa.Numeric(14, 2), nullable=False),
        sa.Column("penalty_applied", sa.Numeric(14, 2), nullable=False),
        sa.Column("excess_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("receipt_number", sa.String(80), nullable=False),
        sa.Column("receipt_version", sa.String(20), nullable=False),
        sa.Column("receipt_snapshot_json", sa.Text(), nullable=False),
        sa.Column("receipt_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], name="fk_payment_reversals_payment_id"),
        sa.ForeignKeyConstraint(["settlement_id"], ["payment_settlements.id"], name="fk_payment_reversals_settlement_id"),
        sa.ForeignKeyConstraint(["admin_id"], ["users.id"], name="fk_payment_reversals_admin_id"),
        sa.UniqueConstraint("payment_id", name="uq_payment_reversals_payment_id"),
        sa.UniqueConstraint("settlement_id", name="uq_payment_reversals_settlement_id"),
        sa.UniqueConstraint("receipt_number", name="uq_payment_reversals_receipt_number"),
        sa.UniqueConstraint("receipt_hash", name="uq_payment_reversals_receipt_hash"),
        sa.CheckConstraint("length(trim(reason)) >= 5", name="ck_payment_reversals_reason"),
        sa.CheckConstraint("amount_received >= 0 AND amount_applied >= 0 AND principal_applied >= 0 AND interest_applied >= 0 AND penalty_applied >= 0 AND excess_amount >= 0", name="ck_payment_reversals_nonnegative_amounts"),
        sa.CheckConstraint("amount_received = amount_applied + excess_amount", name="ck_payment_reversals_received_allocation"),
        sa.CheckConstraint("amount_applied = principal_applied + interest_applied + penalty_applied", name="ck_payment_reversals_applied_components"),
        sa.CheckConstraint("((original_date_kind = 'CONTRIBUTION_COMPETENCE' AND original_competence IS NOT NULL AND original_due_date IS NULL) OR (original_date_kind = 'LOAN_INSTALLMENT_DUE_DATE' AND original_competence IS NULL AND original_due_date IS NOT NULL) OR (original_date_kind = 'AGREEMENT_INSTALLMENT_DUE_DATE' AND original_competence IS NULL AND original_due_date IS NOT NULL))", name="ck_payment_reversals_original_date"),
        sa.CheckConstraint("receipt_version = 'v1'", name="ck_payment_reversals_receipt_version"),
    )
    op.create_index("ix_payment_reversals_admin_id", "payment_reversals", ["admin_id"])
    op.create_index("ix_payment_reversals_reversed_at", "payment_reversals", ["reversed_at"])
    op.create_index("ix_payment_reversals_reversal_competence", "payment_reversals", ["reversal_competence"])

    op.create_table(
        "payment_reversal_components",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payment_reversal_id", sa.Integer(), nullable=False),
        sa.Column("original_ledger_entry_id", sa.Integer(), nullable=False),
        sa.Column("compensating_ledger_entry_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["payment_reversal_id"], ["payment_reversals.id"], name="fk_payment_reversal_components_reversal_id"),
        sa.ForeignKeyConstraint(["original_ledger_entry_id"], ["ledger_entries.id"], name="fk_payment_reversal_components_original_ledger_id"),
        sa.ForeignKeyConstraint(["compensating_ledger_entry_id"], ["ledger_entries.id"], name="fk_payment_reversal_components_compensating_ledger_id"),
        sa.UniqueConstraint("original_ledger_entry_id", name="uq_payment_reversal_components_original_ledger"),
        sa.UniqueConstraint("compensating_ledger_entry_id", name="uq_payment_reversal_components_compensating_ledger"),
        sa.CheckConstraint("original_ledger_entry_id <> compensating_ledger_entry_id", name="ck_payment_reversal_components_distinct_ledgers"),
    )
    op.create_index("ix_payment_reversal_components_reversal_id", "payment_reversal_components", ["payment_reversal_id"])

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("member_financial_entries", recreate="always") as batch_op:
            batch_op.add_column(sa.Column("payment_reversal_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key("fk_member_financial_entries_payment_reversal_id", "payment_reversals", ["payment_reversal_id"], ["id"])
    else:
        op.add_column("member_financial_entries", sa.Column("payment_reversal_id", sa.Integer(), nullable=True))
        op.create_foreign_key("fk_member_financial_entries_payment_reversal_id", "member_financial_entries", "payment_reversals", ["payment_reversal_id"], ["id"])
    op.create_index("uq_member_financial_entries_one_payment_reversal", "member_financial_entries", ["payment_reversal_id"], unique=True, postgresql_where=sa.text("payment_reversal_id IS NOT NULL"), sqlite_where=sa.text("payment_reversal_id IS NOT NULL"))

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("loans", recreate="always") as batch_op:
            batch_op.add_column(sa.Column("state_revision", sa.Integer(), nullable=False, server_default="0"))
            batch_op.create_check_constraint("ck_loans_state_revision_nonnegative", "state_revision >= 0")
    else:
        op.add_column("loans", sa.Column("state_revision", sa.Integer(), nullable=False, server_default="0"))
        op.create_check_constraint("ck_loans_state_revision_nonnegative", "loans", "state_revision >= 0")

    _alter_settlements_for_upgrade(bind)


def downgrade():
    bind = op.get_bind()
    _downgrade_preflight(bind)

    _alter_settlements_for_downgrade(bind)

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("loans", recreate="always") as batch_op:
            batch_op.drop_constraint("ck_loans_state_revision_nonnegative", type_="check")
            batch_op.drop_column("state_revision")
    else:
        op.drop_constraint("ck_loans_state_revision_nonnegative", "loans", type_="check")
        op.drop_column("loans", "state_revision")

    op.drop_index("uq_member_financial_entries_one_payment_reversal", table_name="member_financial_entries")
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("member_financial_entries", recreate="always") as batch_op:
            batch_op.drop_constraint("fk_member_financial_entries_payment_reversal_id", type_="foreignkey")
            batch_op.drop_column("payment_reversal_id")
    else:
        op.drop_constraint("fk_member_financial_entries_payment_reversal_id", "member_financial_entries", type_="foreignkey")
        op.drop_column("member_financial_entries", "payment_reversal_id")

    op.drop_index("ix_payment_reversal_components_reversal_id", table_name="payment_reversal_components")
    op.drop_table("payment_reversal_components")
    for name in ("ix_payment_reversals_reversal_competence", "ix_payment_reversals_reversed_at", "ix_payment_reversals_admin_id"):
        op.drop_index(name, table_name="payment_reversals")
    op.drop_table("payment_reversals")
