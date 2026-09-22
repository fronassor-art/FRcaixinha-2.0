"""add idempotent late-interest event contract"""

from alembic import op
import sqlalchemy as sa


revision = "0094_late_interest_event_contract"
down_revision = "0093_payment_attempt_schema_foundation"
branch_labels = None
depends_on = None


OLD_EVENT_TYPE_CHECK = (
    "event_type IN ('FIXED_PENALTY_ASSESSED', 'LATE_INTEREST_ACCRUED', "
    "'PRINCIPAL_BASE_REDUCED', 'PRINCIPAL_BASE_RESTORED')"
)
NEW_EVENT_TYPE_CHECK = (
    "event_type IN ('FIXED_PENALTY_ASSESSED', 'LATE_INTEREST_ACCRUED', "
    "'PRINCIPAL_BASE_REDUCED', 'PRINCIPAL_BASE_RESTORED', "
    "'LATE_INTEREST_ADJUSTMENT_INCREASE', "
    "'LATE_INTEREST_ADJUSTMENT_DECREASE')"
)
ADJUSTMENT_TYPES = (
    "'LATE_INTEREST_ADJUSTMENT_INCREASE', ",
    "'LATE_INTEREST_ADJUSTMENT_DECREASE'",
)

EVENT_TYPE_CHECK = "ck_loan_late_charge_events_type"
ADJUSTMENT_AMOUNT_CHECK = "ck_loan_late_charge_events_adjustment_amount"
ADJUSTMENT_PRINCIPAL_CHECK = "ck_loan_late_charge_events_adjustment_no_principal"
ADJUSTMENT_CAUSE_CHECK = "ck_loan_late_charge_events_adjustment_cause"
DAILY_INDEX = "uq_llce_late_interest_day"
SETTLEMENT_INDEX = "uq_llce_adjustment_settlement"
REVERSAL_INDEX = "uq_llce_adjustment_reversal"


def _adjustment_checks(target, table_name=None):
    adjustment_types = f"({ADJUSTMENT_TYPES[0]}{ADJUSTMENT_TYPES[1]})"
    checks = (
        (
            ADJUSTMENT_AMOUNT_CHECK,
            f"event_type NOT IN {adjustment_types} OR amount IS NOT NULL",
        ),
        (
            ADJUSTMENT_PRINCIPAL_CHECK,
            f"event_type NOT IN {adjustment_types} OR eligible_principal IS NULL",
        ),
        (
            ADJUSTMENT_CAUSE_CHECK,
            f"event_type NOT IN {adjustment_types} OR "
            "((payment_settlement_id IS NOT NULL AND payment_reversal_id IS NULL) OR "
            "(payment_settlement_id IS NULL AND payment_reversal_id IS NOT NULL))",
        ),
    )
    for name, expression in checks:
        if table_name is None:
            target.create_check_constraint(name, expression)
        else:
            target.create_check_constraint(name, table_name, expression)


def _drop_adjustment_checks(target, table_name=None):
    for name in (
        ADJUSTMENT_CAUSE_CHECK,
        ADJUSTMENT_PRINCIPAL_CHECK,
        ADJUSTMENT_AMOUNT_CHECK,
    ):
        if table_name is None:
            target.drop_constraint(name, type_="check")
        else:
            target.drop_constraint(name, table_name, type_="check")


def _alter_event_checks(*, upgrade: bool):
    bind = op.get_bind()
    event_type_check = NEW_EVENT_TYPE_CHECK if upgrade else OLD_EVENT_TYPE_CHECK
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("loan_late_charge_events", recreate="always") as batch_op:
            if upgrade:
                batch_op.drop_constraint(EVENT_TYPE_CHECK, type_="check")
                batch_op.create_check_constraint(EVENT_TYPE_CHECK, event_type_check)
                _adjustment_checks(batch_op)
            else:
                _drop_adjustment_checks(batch_op)
                batch_op.drop_constraint(EVENT_TYPE_CHECK, type_="check")
                batch_op.create_check_constraint(EVENT_TYPE_CHECK, event_type_check)
    else:
        op.drop_constraint(EVENT_TYPE_CHECK, "loan_late_charge_events", type_="check")
        op.create_check_constraint(EVENT_TYPE_CHECK, "loan_late_charge_events", event_type_check)
        if upgrade:
            _adjustment_checks(op, "loan_late_charge_events")
        else:
            _drop_adjustment_checks(op, "loan_late_charge_events")


def _create_indexes():
    op.create_index(
        DAILY_INDEX,
        "loan_late_charge_events",
        ["loan_installment_id", "late_charge_version", "effective_date"],
        unique=True,
        sqlite_where=sa.text("event_type = 'LATE_INTEREST_ACCRUED'"),
        postgresql_where=sa.text("event_type = 'LATE_INTEREST_ACCRUED'"),
    )
    op.create_index(
        SETTLEMENT_INDEX,
        "loan_late_charge_events",
        ["loan_installment_id", "late_charge_version", "payment_settlement_id"],
        unique=True,
        sqlite_where=sa.text(
            "event_type IN ('LATE_INTEREST_ADJUSTMENT_INCREASE', "
            "'LATE_INTEREST_ADJUSTMENT_DECREASE') AND "
            "payment_settlement_id IS NOT NULL"
        ),
        postgresql_where=sa.text(
            "event_type IN ('LATE_INTEREST_ADJUSTMENT_INCREASE', "
            "'LATE_INTEREST_ADJUSTMENT_DECREASE') AND "
            "payment_settlement_id IS NOT NULL"
        ),
    )
    op.create_index(
        REVERSAL_INDEX,
        "loan_late_charge_events",
        ["loan_installment_id", "late_charge_version", "payment_reversal_id"],
        unique=True,
        sqlite_where=sa.text(
            "event_type IN ('LATE_INTEREST_ADJUSTMENT_INCREASE', "
            "'LATE_INTEREST_ADJUSTMENT_DECREASE') AND "
            "payment_reversal_id IS NOT NULL"
        ),
        postgresql_where=sa.text(
            "event_type IN ('LATE_INTEREST_ADJUSTMENT_INCREASE', "
            "'LATE_INTEREST_ADJUSTMENT_DECREASE') AND "
            "payment_reversal_id IS NOT NULL"
        ),
    )


def _drop_indexes():
    op.drop_index(REVERSAL_INDEX, table_name="loan_late_charge_events")
    op.drop_index(SETTLEMENT_INDEX, table_name="loan_late_charge_events")
    op.drop_index(DAILY_INDEX, table_name="loan_late_charge_events")


def _downgrade_guard():
    bind = op.get_bind()
    count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM loan_late_charge_events WHERE event_type IN "
            "('LATE_INTEREST_ADJUSTMENT_INCREASE', "
            "'LATE_INTEREST_ADJUSTMENT_DECREASE')"
        )
    ).scalar_one()
    if int(count):
        raise RuntimeError(
            "Cannot downgrade 0094: late-interest adjustment events exist"
        )


def upgrade():
    _alter_event_checks(upgrade=True)
    _create_indexes()


def downgrade():
    _downgrade_guard()
    _drop_indexes()
    _alter_event_checks(upgrade=False)
