"""add durable scheduler run and unit claims"""

from alembic import op
import sqlalchemy as sa


revision = "0104_scheduler_runs_g3d4a"
down_revision = "0103_pix_reconciliation_schema_a377b4r4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "scheduler_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_key", sa.String(length=100), nullable=False),
        sa.Column("financial_date", sa.Date(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_scheduler_runs"),
        sa.UniqueConstraint("job_key", "financial_date", name="uq_scheduler_runs_job_financial_date"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_scheduler_runs_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_scheduler_runs_attempt_nonnegative"),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(status != 'RUNNING' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_scheduler_runs_lease_state",
        ),
        sa.CheckConstraint(
            "(status = 'SUCCEEDED' AND completed_at IS NOT NULL) OR "
            "(status != 'SUCCEEDED' AND completed_at IS NULL)",
            name="ck_scheduler_runs_completion_state",
        ),
    )
    op.create_index(
        "ix_scheduler_runs_status_lease",
        "scheduler_runs",
        ["status", "lease_expires_at"],
        unique=False,
    )

    op.create_table(
        "scheduler_run_units",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("unit_key", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_scheduler_run_units"),
        sa.ForeignKeyConstraint(["run_id"], ["scheduler_runs.id"], name="fk_scheduler_run_units_run_id_scheduler_runs", ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "unit_key", name="uq_scheduler_run_units_run_unit"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_scheduler_run_units_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_scheduler_run_units_attempt_nonnegative"),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(status != 'RUNNING' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_scheduler_run_units_lease_state",
        ),
        sa.CheckConstraint(
            "(status = 'SUCCEEDED' AND completed_at IS NOT NULL) OR "
            "(status != 'SUCCEEDED' AND completed_at IS NULL)",
            name="ck_scheduler_run_units_completion_state",
        ),
    )
    op.create_index(
        "ix_scheduler_run_units_run_status_lease",
        "scheduler_run_units",
        ["run_id", "status", "lease_expires_at"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_scheduler_run_units_run_status_lease", table_name="scheduler_run_units")
    op.drop_table("scheduler_run_units")
    op.drop_index("ix_scheduler_runs_status_lease", table_name="scheduler_runs")
    op.drop_table("scheduler_runs")
