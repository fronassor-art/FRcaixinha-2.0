"""A3.77B1: cycle participation and frozen contribution charges."""

from alembic import op
import sqlalchemy as sa

revision = "0097_cycle_participation_a377b1"
down_revision = "0096_cycle_foundation_a377a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("contributions", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("contributions", sa.Column("cancellation_reason", sa.String(80), nullable=True))
    op.create_table(
        "cycle_participations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cycle_id", sa.Integer(), sa.ForeignKey("cycles.id", name="fk_cycle_participations_cycle"), nullable=False),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", name="fk_cycle_participations_member"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="ACTIVE"),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voluntary_exit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("block_reason", sa.String(80), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_cycle_participations"),
        sa.UniqueConstraint("member_id", "cycle_id", name="uq_cycle_participations_member_cycle"),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND voluntary_exit_at IS NULL AND blocked_at IS NULL AND block_reason IS NULL) OR "
            "(status = 'VOLUNTARILY_EXITED' AND voluntary_exit_at IS NOT NULL AND blocked_at IS NULL AND block_reason IS NULL) OR "
            "(status = 'BLOCKED_DELINQUENCY' AND voluntary_exit_at IS NULL AND blocked_at IS NOT NULL AND block_reason IS NOT NULL)",
            name="ck_cycle_participations_state",
        ),
    )
    op.create_index("ix_cycle_participations_cycle_status", "cycle_participations", ["cycle_id", "status"])
    op.create_table(
        "contribution_charge_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contribution_id", sa.Integer(), sa.ForeignKey("contributions.id", name="fk_contribution_charge_events_contribution"), nullable=False),
        sa.Column("participation_id", sa.Integer(), sa.ForeignKey("cycle_participations.id", name="fk_contribution_charge_events_participation"), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("rule_version", sa.String(40), nullable=False),
        sa.Column("accrued_through", sa.Date(), nullable=False),
        sa.Column("fixed_penalty", sa.Numeric(14, 2), nullable=False),
        sa.Column("daily_interest", sa.Numeric(14, 2), nullable=False),
        sa.Column("cancelled_principal", sa.Numeric(14, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_contribution_charge_events"),
        sa.UniqueConstraint("contribution_id", "event_type", "accrued_through", name="uq_contribution_charge_events_snapshot"),
        sa.CheckConstraint("event_type IN ('ACCRUAL_SNAPSHOT', 'BLOCK_FREEZE')", name="ck_contribution_charge_events_type"),
        sa.CheckConstraint(
            "fixed_penalty >= 0 AND daily_interest >= 0 AND cancelled_principal >= 0",
            name="ck_contribution_charge_events_nonnegative",
        ),
    )
    op.create_index("ix_contribution_charge_events_participation", "contribution_charge_events", ["participation_id"])
    # A3.77A quotas are explicit cycle membership evidence. No global Member.status inference.
    op.execute(sa.text(
        "INSERT INTO cycle_participations (cycle_id, member_id, status) "
        "SELECT DISTINCT cycle_id, member_id, 'ACTIVE' FROM quotas WHERE cycle_id IS NOT NULL"
    ))


def downgrade():
    bind = op.get_bind()
    created_or_transitioned = bind.execute(sa.text(
        "SELECT COUNT(*) FROM cycle_participations "
        "WHERE status != 'ACTIVE' OR joined_at IS NOT NULL"
    )).scalar_one()
    charges = bind.execute(sa.text("SELECT COUNT(*) FROM contribution_charge_events")).scalar_one()
    cancelled = bind.execute(sa.text("SELECT COUNT(*) FROM contributions WHERE cancelled_at IS NOT NULL")).scalar_one()
    if created_or_transitioned or charges or cancelled:
        raise RuntimeError("A3.77B1 data exists; refusing destructive downgrade")
    op.drop_index("ix_contribution_charge_events_participation", table_name="contribution_charge_events")
    op.drop_table("contribution_charge_events")
    op.drop_index("ix_cycle_participations_cycle_status", table_name="cycle_participations")
    op.drop_table("cycle_participations")
    op.drop_column("contributions", "cancellation_reason")
    op.drop_column("contributions", "cancelled_at")
