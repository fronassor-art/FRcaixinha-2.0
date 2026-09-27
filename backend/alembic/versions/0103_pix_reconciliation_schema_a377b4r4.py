"""allow durable PIX placeholders and persist webhook resource identity"""

from alembic import op
import sqlalchemy as sa


revision = "0103_pix_reconciliation_schema_a377b4r4"
down_revision = "0102_payout_verification_evidence_a377b4r3"
branch_labels = None
depends_on = None


PAYMENT_CHECK = "ck_payments_provider_id_or_versioned_loan_attempt"
PAYMENT_CHECK_SQL = (
    "provider_payment_id IS NOT NULL OR "
    "(reference_type = 'CONTRIBUTION' AND reference_id IS NOT NULL "
    "AND idempotency_key IS NOT NULL AND attempt_status IS NOT NULL) OR "
    "(reference_type = 'AGREEMENT_INSTALLMENT' AND reference_id IS NOT NULL "
    "AND idempotency_key IS NOT NULL AND attempt_status IS NOT NULL) OR "
    "(reference_type = 'LOAN_INSTALLMENT' AND reference_id IS NOT NULL "
    "AND attempt_status IS NOT NULL AND idempotency_key IS NOT NULL "
    "AND calculated_for_date IS NOT NULL AND financial_snapshot_json IS NOT NULL "
    "AND snapshot_hash IS NOT NULL AND expires_at IS NOT NULL)"
)


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("payments", recreate="always") as batch_op:
            batch_op.drop_constraint(PAYMENT_CHECK, type_="check")
            batch_op.create_check_constraint(PAYMENT_CHECK, PAYMENT_CHECK_SQL)
    else:
        op.drop_constraint(PAYMENT_CHECK, "payments", type_="check")
        op.create_check_constraint(PAYMENT_CHECK, "payments", PAYMENT_CHECK_SQL)

    op.add_column(
        "webhook_events",
        sa.Column("resource_id", sa.String(length=150), nullable=True),
    )


def downgrade():
    bind = op.get_bind()
    invalid_legacy_rows = bind.execute(sa.text("""
        SELECT COUNT(*) FROM payments
        WHERE provider_payment_id IS NULL
          AND (
              reference_type IS NULL
              OR reference_type <> 'LOAN_INSTALLMENT'
              OR reference_id IS NULL
              OR attempt_status IS NULL
              OR idempotency_key IS NULL
              OR calculated_for_date IS NULL
              OR financial_snapshot_json IS NULL
              OR snapshot_hash IS NULL
              OR expires_at IS NULL
          )
    """)).scalar_one()
    if int(invalid_legacy_rows):
        raise RuntimeError("Cannot downgrade 0103: non-loan or incomplete PIX placeholders exist")

    resource_ids = bind.execute(sa.text(
        "SELECT COUNT(*) FROM webhook_events WHERE resource_id IS NOT NULL"
    )).scalar_one()
    if int(resource_ids):
        raise RuntimeError("Cannot downgrade 0103: webhook resource IDs would be lost")

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("payments", recreate="always") as batch_op:
            batch_op.drop_constraint(PAYMENT_CHECK, type_="check")
            batch_op.create_check_constraint(
                PAYMENT_CHECK,
                "provider_payment_id IS NOT NULL OR "
                "(reference_type = 'LOAN_INSTALLMENT' AND reference_id IS NOT NULL "
                "AND attempt_status IS NOT NULL AND idempotency_key IS NOT NULL "
                "AND calculated_for_date IS NOT NULL AND financial_snapshot_json IS NOT NULL "
                "AND snapshot_hash IS NOT NULL AND expires_at IS NOT NULL)",
            )
        with op.batch_alter_table("webhook_events", recreate="always") as batch_op:
            batch_op.drop_column("resource_id")
    else:
        op.drop_column("webhook_events", "resource_id")
        op.drop_constraint(PAYMENT_CHECK, "payments", type_="check")
        op.create_check_constraint(
            PAYMENT_CHECK,
            "payments",
            "provider_payment_id IS NOT NULL OR "
            "(reference_type = 'LOAN_INSTALLMENT' AND reference_id IS NOT NULL "
            "AND attempt_status IS NOT NULL AND idempotency_key IS NOT NULL "
            "AND calculated_for_date IS NOT NULL AND financial_snapshot_json IS NOT NULL "
            "AND snapshot_hash IS NOT NULL AND expires_at IS NOT NULL)",
        )
