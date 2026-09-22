"""allow durable versioned PIX reservations before provider binding"""

from alembic import op
import sqlalchemy as sa

revision = "0095_pix_attempt_provider_reservation"
down_revision = "0094_late_interest_event_contract"
branch_labels = None
depends_on = None

CHECK_NAME = "ck_payments_provider_id_or_versioned_loan_attempt"
# A NULL provider id is permitted only for a versioned LoanInstallment attempt.
# Terminal local attempts remain auditable when a create failure is unequivocal.
CHECK = "provider_payment_id IS NOT NULL OR (reference_type = 'LOAN_INSTALLMENT' AND reference_id IS NOT NULL AND attempt_status IS NOT NULL AND idempotency_key IS NOT NULL AND calculated_for_date IS NOT NULL AND financial_snapshot_json IS NOT NULL AND snapshot_hash IS NOT NULL AND expires_at IS NOT NULL)"

def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("payments", recreate="always") as batch_op:
            batch_op.alter_column("provider_payment_id", existing_type=sa.String(150), nullable=True)
            batch_op.create_check_constraint(CHECK_NAME, CHECK)
    else:
        op.alter_column("payments", "provider_payment_id", existing_type=sa.String(150), nullable=True)
        op.create_check_constraint(CHECK_NAME, "payments", CHECK)

def downgrade():
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT COUNT(*) FROM payments WHERE provider_payment_id IS NULL")).scalar_one()
    if int(count):
        raise RuntimeError("Cannot downgrade 0095: durable provider reservations exist")
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("payments", recreate="always") as batch_op:
            batch_op.drop_constraint(CHECK_NAME, type_="check")
            batch_op.alter_column("provider_payment_id", existing_type=sa.String(150), nullable=False)
    else:
        op.drop_constraint(CHECK_NAME, "payments", type_="check")
        op.alter_column("payments", "provider_payment_id", existing_type=sa.String(150), nullable=False)
