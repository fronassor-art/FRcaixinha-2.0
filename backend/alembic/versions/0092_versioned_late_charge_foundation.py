"""add versioned late-charge schema foundation"""

from alembic import op
import sqlalchemy as sa


revision = "0092_versioned_late_charge_foundation"
down_revision = "0091_loan_calculation_version"
branch_labels = None
depends_on = None


LATE_CHARGE_SETTLEMENT_COMPONENT_VERSION = "late_charge_components_v1"
FIXED_PENALTY_INDEX = "uq_loan_late_charge_events_fixed_penalty"

_INSTALLMENT_COLUMNS = (
    "late_charge_version",
    "fixed_penalty_amount",
    "paid_fixed_penalty_amount",
    "late_interest_amount",
    "paid_late_interest_amount",
    "late_interest_accrued_through_date",
)
_SETTLEMENT_COLUMNS = (
    "settlement_component_version",
    "normal_interest_applied",
    "late_interest_applied",
    "fixed_penalty_applied",
)
_INSTALLMENT_CHECKS = (
    (
        "ck_loan_installments_late_charge_version",
        "late_charge_version IS NULL OR length(trim(late_charge_version)) > 0",
    ),
    (
        "ck_loan_installments_late_charge_presence",
        "(late_charge_version IS NULL AND fixed_penalty_amount IS NULL AND "
        "paid_fixed_penalty_amount IS NULL AND late_interest_amount IS NULL AND "
        "paid_late_interest_amount IS NULL AND late_interest_accrued_through_date IS NULL) OR "
        "(late_charge_version IS NOT NULL AND fixed_penalty_amount IS NOT NULL AND "
        "paid_fixed_penalty_amount IS NOT NULL AND late_interest_amount IS NOT NULL AND "
        "paid_late_interest_amount IS NOT NULL)",
    ),
    (
        "ck_loan_installments_fixed_penalty_nonnegative",
        "fixed_penalty_amount IS NULL OR fixed_penalty_amount >= 0",
    ),
    (
        "ck_loan_installments_paid_fixed_penalty_nonnegative",
        "paid_fixed_penalty_amount IS NULL OR paid_fixed_penalty_amount >= 0",
    ),
    (
        "ck_loan_installments_late_interest_nonnegative",
        "late_interest_amount IS NULL OR late_interest_amount >= 0",
    ),
    (
        "ck_loan_installments_paid_late_interest_nonnegative",
        "paid_late_interest_amount IS NULL OR paid_late_interest_amount >= 0",
    ),
    (
        "ck_loan_installments_paid_fixed_penalty_lte_assessed",
        "paid_fixed_penalty_amount IS NULL OR paid_fixed_penalty_amount <= fixed_penalty_amount",
    ),
    (
        "ck_loan_installments_paid_late_interest_lte_accrued",
        "paid_late_interest_amount IS NULL OR paid_late_interest_amount <= late_interest_amount",
    ),
)
_SETTLEMENT_CHECKS = (
    (
        "ck_payment_settlements_component_version",
        "settlement_component_version IS NULL OR "
        f"settlement_component_version = '{LATE_CHARGE_SETTLEMENT_COMPONENT_VERSION}'",
    ),
    (
        "ck_payment_settlements_component_presence",
        "(settlement_component_version IS NULL AND normal_interest_applied IS NULL AND "
        "late_interest_applied IS NULL AND fixed_penalty_applied IS NULL) OR "
        "(settlement_component_version IS NOT NULL AND normal_interest_applied IS NOT NULL AND "
        "late_interest_applied IS NOT NULL AND fixed_penalty_applied IS NOT NULL)",
    ),
    (
        "ck_payment_settlements_normal_interest_nonnegative",
        "normal_interest_applied IS NULL OR normal_interest_applied >= 0",
    ),
    (
        "ck_payment_settlements_late_interest_nonnegative",
        "late_interest_applied IS NULL OR late_interest_applied >= 0",
    ),
    (
        "ck_payment_settlements_fixed_penalty_nonnegative",
        "fixed_penalty_applied IS NULL OR fixed_penalty_applied >= 0",
    ),
    (
        "ck_payment_settlements_components_loan_only",
        "settlement_component_version IS NULL OR obligation_type = 'LOAN_INSTALLMENT'",
    ),
    (
        "ck_payment_settlements_component_aggregates",
        "settlement_component_version IS NULL OR "
        "(interest_applied = normal_interest_applied AND "
        "penalty_applied = late_interest_applied + fixed_penalty_applied)",
    ),
)


def _add_columns(target, table_name, columns):
    for column in columns:
        if table_name is None:
            target.add_column(column)
        else:
            target.add_column(table_name, column)


def _add_installment_columns(target, table_name=None):
    _add_columns(
        target,
        table_name,
        (
            sa.Column("late_charge_version", sa.String(60), nullable=True),
            sa.Column("fixed_penalty_amount", sa.Numeric(14, 2), nullable=True),
            sa.Column("paid_fixed_penalty_amount", sa.Numeric(14, 2), nullable=True),
            sa.Column("late_interest_amount", sa.Numeric(14, 2), nullable=True),
            sa.Column("paid_late_interest_amount", sa.Numeric(14, 2), nullable=True),
            sa.Column("late_interest_accrued_through_date", sa.Date(), nullable=True),
        ),
    )


def _add_settlement_columns(target, table_name=None):
    _add_columns(
        target,
        table_name,
        (
            sa.Column("settlement_component_version", sa.String(60), nullable=True),
            sa.Column("normal_interest_applied", sa.Numeric(14, 2), nullable=True),
            sa.Column("late_interest_applied", sa.Numeric(14, 2), nullable=True),
            sa.Column("fixed_penalty_applied", sa.Numeric(14, 2), nullable=True),
        ),
    )


def _create_checks(target, table_name, checks):
    for name, expression in checks:
        if table_name is None:
            target.create_check_constraint(name, expression)
        else:
            target.create_check_constraint(name, table_name, expression)


def _create_event_table():
    op.create_table(
        "loan_late_charge_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("loan_installment_id", sa.Integer(), nullable=False),
        sa.Column("late_charge_version", sa.String(60), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("eligible_principal", sa.Numeric(14, 2), nullable=True),
        sa.Column("payment_settlement_id", sa.Integer(), nullable=True),
        sa.Column("payment_reversal_id", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["loan_installment_id"],
            ["loan_installments.id"],
            name="fk_loan_late_charge_events_installment_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payment_settlement_id"],
            ["payment_settlements.id"],
            name="fk_loan_late_charge_events_settlement_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payment_reversal_id"],
            ["payment_reversals.id"],
            name="fk_loan_late_charge_events_reversal_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "length(trim(late_charge_version)) > 0",
            name="ck_loan_late_charge_events_version_nonempty",
        ),
        sa.CheckConstraint(
            "event_type IN ('FIXED_PENALTY_ASSESSED', 'LATE_INTEREST_ACCRUED', "
            "'PRINCIPAL_BASE_REDUCED', 'PRINCIPAL_BASE_RESTORED')",
            name="ck_loan_late_charge_events_type",
        ),
        sa.CheckConstraint(
            "amount IS NULL OR amount >= 0",
            name="ck_loan_late_charge_events_amount_nonnegative",
        ),
        sa.CheckConstraint(
            "eligible_principal IS NULL OR eligible_principal >= 0",
            name="ck_loan_late_charge_events_principal_nonnegative",
        ),
        sa.CheckConstraint(
            "event_type != 'FIXED_PENALTY_ASSESSED' OR amount IS NOT NULL",
            name="ck_loan_late_charge_events_fixed_penalty_amount",
        ),
        sa.CheckConstraint(
            "event_type != 'LATE_INTEREST_ACCRUED' OR "
            "(amount IS NOT NULL AND eligible_principal IS NOT NULL)",
            name="ck_loan_late_charge_events_accrual_values",
        ),
    )
    op.create_index(
        FIXED_PENALTY_INDEX,
        "loan_late_charge_events",
        ["loan_installment_id", "late_charge_version"],
        unique=True,
        sqlite_where=sa.text("event_type = 'FIXED_PENALTY_ASSESSED'"),
        postgresql_where=sa.text("event_type = 'FIXED_PENALTY_ASSESSED'"),
    )
    op.create_index(
        "ix_loan_late_charge_events_installment_effective",
        "loan_late_charge_events",
        ["loan_installment_id", "effective_date"],
        unique=False,
    )
    op.create_index(
        "ix_loan_late_charge_events_settlement_id",
        "loan_late_charge_events",
        ["payment_settlement_id"],
        unique=False,
    )
    op.create_index(
        "ix_loan_late_charge_events_reversal_id",
        "loan_late_charge_events",
        ["payment_reversal_id"],
        unique=False,
    )


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("loan_installments", recreate="always") as batch_op:
            _add_installment_columns(batch_op)
            _create_checks(batch_op, None, _INSTALLMENT_CHECKS)
        with op.batch_alter_table("payment_settlements", recreate="always") as batch_op:
            _add_settlement_columns(batch_op)
            _create_checks(batch_op, None, _SETTLEMENT_CHECKS)
    else:
        _add_installment_columns(op, "loan_installments")
        _create_checks(op, "loan_installments", _INSTALLMENT_CHECKS)
        _add_settlement_columns(op, "payment_settlements")
        _create_checks(op, "payment_settlements", _SETTLEMENT_CHECKS)
    _create_event_table()


def _preflight_downgrade(bind):
    event_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM loan_late_charge_events")
    ).scalar_one()
    installment_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM loan_installments WHERE "
            + " OR ".join(f"{column} IS NOT NULL" for column in _INSTALLMENT_COLUMNS)
        )
    ).scalar_one()
    settlement_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM payment_settlements WHERE "
            + " OR ".join(f"{column} IS NOT NULL" for column in _SETTLEMENT_COLUMNS)
        )
    ).scalar_one()
    if int(event_count) or int(installment_count) or int(settlement_count):
        raise RuntimeError(
            "Cannot downgrade 0092: versioned late-charge data exists"
        )


def downgrade():
    bind = op.get_bind()
    _preflight_downgrade(bind)
    op.drop_table("loan_late_charge_events")
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("payment_settlements", recreate="always") as batch_op:
            for name, _ in _SETTLEMENT_CHECKS:
                batch_op.drop_constraint(name, type_="check")
            for column in reversed(_SETTLEMENT_COLUMNS):
                batch_op.drop_column(column)
        with op.batch_alter_table("loan_installments", recreate="always") as batch_op:
            for name, _ in _INSTALLMENT_CHECKS:
                batch_op.drop_constraint(name, type_="check")
            for column in reversed(_INSTALLMENT_COLUMNS):
                batch_op.drop_column(column)
    else:
        for name, _ in _SETTLEMENT_CHECKS:
            op.drop_constraint(name, "payment_settlements", type_="check")
        for column in reversed(_SETTLEMENT_COLUMNS):
            op.drop_column("payment_settlements", column)
        for name, _ in _INSTALLMENT_CHECKS:
            op.drop_constraint(name, "loan_installments", type_="check")
        for column in reversed(_INSTALLMENT_COLUMNS):
            op.drop_column("loan_installments", column)
