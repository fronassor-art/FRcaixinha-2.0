"""A3.77B2 R2: annual closing persistence and typed realized external gains."""

from alembic import op
import sqlalchemy as sa


revision = "0098_cycle_closing_persistence_a377b2"
down_revision = "0097_cycle_participation_a377b1"
branch_labels = None
depends_on = None


def _create_sqlite_guards(bind):
    bind.exec_driver_sql(
        """
        CREATE TRIGGER trg_cycle_annual_snapshots_no_update
        BEFORE UPDATE ON cycle_annual_closing_snapshots
        BEGIN
            SELECT RAISE(ABORT, 'annual closing snapshots are immutable');
        END
        """
    )
    bind.exec_driver_sql(
        """
        CREATE TRIGGER trg_cycle_annual_snapshots_no_delete
        BEFORE DELETE ON cycle_annual_closing_snapshots
        BEGIN
            SELECT RAISE(ABORT, 'annual closing snapshots are immutable');
        END
        """
    )
    bind.exec_driver_sql(
        """
        CREATE TRIGGER trg_cycle_gain_events_no_update
        BEFORE UPDATE ON cycle_realized_gain_events
        BEGIN
            SELECT RAISE(ABORT, 'realized gain events are immutable');
        END
        """
    )
    bind.exec_driver_sql(
        """
        CREATE TRIGGER trg_cycle_gain_events_no_delete
        BEFORE DELETE ON cycle_realized_gain_events
        BEGIN
            SELECT RAISE(ABORT, 'realized gain events are immutable');
        END
        """
    )
    bind.exec_driver_sql(
        """
        CREATE TRIGGER trg_cycle_gain_events_full_reversal
        BEFORE INSERT ON cycle_realized_gain_events
        WHEN NEW.reversal_of_id IS NOT NULL
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM cycle_realized_gain_events AS original
                WHERE original.id = NEW.reversal_of_id
                  AND original.reversal_of_id IS NULL
                  AND original.cycle_id = NEW.cycle_id
                  AND original.event_type = NEW.event_type
                  AND original.amount = NEW.amount
                  AND original.realized_at <= NEW.realized_at
            ) THEN RAISE(ABORT, 'gain reversal must fully compensate matching original') END;
        END
        """
    )


def _create_postgresql_guards(bind):
    bind.exec_driver_sql(
        """
        CREATE FUNCTION cycle_annual_snapshot_immutable_guard()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'annual closing snapshots are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    bind.exec_driver_sql(
        """
        CREATE TRIGGER trg_cycle_annual_snapshots_immutable
        BEFORE UPDATE OR DELETE ON cycle_annual_closing_snapshots
        FOR EACH ROW EXECUTE FUNCTION cycle_annual_snapshot_immutable_guard()
        """
    )
    bind.exec_driver_sql(
        """
        CREATE FUNCTION cycle_gain_event_immutable_guard()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'realized gain events are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    bind.exec_driver_sql(
        """
        CREATE TRIGGER trg_cycle_gain_events_immutable
        BEFORE UPDATE OR DELETE ON cycle_realized_gain_events
        FOR EACH ROW EXECUTE FUNCTION cycle_gain_event_immutable_guard()
        """
    )
    bind.exec_driver_sql(
        """
        CREATE FUNCTION cycle_gain_event_full_reversal_guard()
        RETURNS trigger AS $$
        DECLARE original cycle_realized_gain_events%%ROWTYPE;
        BEGIN
            IF NEW.reversal_of_id IS NOT NULL THEN
                SELECT * INTO original
                FROM cycle_realized_gain_events
                WHERE id = NEW.reversal_of_id
                FOR KEY SHARE;
                IF NOT FOUND
                   OR original.reversal_of_id IS NOT NULL
                   OR original.cycle_id <> NEW.cycle_id
                   OR original.event_type <> NEW.event_type
                   OR original.amount <> NEW.amount
                   OR original.realized_at > NEW.realized_at THEN
                    RAISE EXCEPTION 'gain reversal must fully compensate matching original';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    bind.exec_driver_sql(
        """
        CREATE TRIGGER trg_cycle_gain_events_full_reversal
        BEFORE INSERT ON cycle_realized_gain_events
        FOR EACH ROW EXECUTE FUNCTION cycle_gain_event_full_reversal_guard()
        """
    )


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        with op.batch_alter_table("loans", recreate="always") as batch_op:
            batch_op.add_column(sa.Column("cycle_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_loans_member_cycle_participation",
                "cycle_participations",
                ["member_id", "cycle_id"],
                ["member_id", "cycle_id"],
                ondelete="RESTRICT",
            )
    else:
        op.add_column("loans", sa.Column("cycle_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_loans_member_cycle_participation",
            "loans",
            "cycle_participations",
            ["member_id", "cycle_id"],
            ["member_id", "cycle_id"],
            ondelete="RESTRICT",
        )
    op.create_index("ix_loans_cycle_id", "loans", ["cycle_id"], unique=False)

    op.create_table(
        "cycle_annual_closings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cycle_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="ASSESSING"),
        sa.Column("state_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Integer(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["cycle_id"], ["cycles.id"], name="fk_cycle_annual_closings_cycle", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_cycle_annual_closings_created_by", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name="fk_cycle_annual_closings_updated_by", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], name="fk_cycle_annual_closings_approved_by", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["closed_by"], ["users.id"], name="fk_cycle_annual_closings_closed_by", ondelete="RESTRICT"),
        sa.UniqueConstraint("cycle_id", name="uq_cycle_annual_closings_cycle"),
        sa.UniqueConstraint("id", "cycle_id", name="uq_cycle_annual_closings_id_cycle"),
        sa.CheckConstraint(
            "status IN ('ASSESSING', 'READY_FOR_REVIEW', 'MASTER_APPROVED', 'CLOSED', 'PAYING', 'LIQUIDATED')",
            name="ck_cycle_annual_closings_status",
        ),
        sa.CheckConstraint("state_revision >= 0", name="ck_cycle_annual_closings_revision"),
    )
    op.create_index("ix_cycle_annual_closings_status", "cycle_annual_closings", ["status"], unique=False)

    op.create_table(
        "cycle_annual_closing_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("closing_id", sa.Integer(), nullable=False),
        sa.Column("cycle_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_version", sa.String(length=60), nullable=False),
        sa.Column("closing_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calculation_version", sa.String(length=60), nullable=False),
        sa.Column("canonical_payload", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("gross_realized_result", sa.Numeric(14, 2), nullable=False),
        sa.Column("administration_fee_rate", sa.Numeric(5, 4), nullable=False),
        sa.Column("administration_fee", sa.Numeric(14, 2), nullable=False),
        sa.Column("distributable_result", sa.Numeric(14, 2), nullable=False),
        sa.Column("total_eligible_contributions", sa.Numeric(14, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["closing_id", "cycle_id"],
            ["cycle_annual_closings.id", "cycle_annual_closings.cycle_id"],
            name="fk_cycle_annual_snapshots_closing_cycle", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["cycle_id"], ["cycles.id"], name="fk_cycle_annual_snapshots_cycle", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_cycle_annual_snapshots_created_by", ondelete="RESTRICT"),
        sa.UniqueConstraint("closing_id", name="uq_cycle_annual_snapshots_closing"),
        sa.UniqueConstraint("cycle_id", name="uq_cycle_annual_snapshots_cycle"),
        sa.UniqueConstraint("payload_hash", name="uq_cycle_annual_snapshots_hash"),
        sa.CheckConstraint("length(payload_hash) = 64", name="ck_cycle_annual_snapshots_hash_length"),
        sa.CheckConstraint(
            "gross_realized_result >= 0 AND administration_fee >= 0 AND distributable_result >= 0 "
            "AND total_eligible_contributions >= 0 AND administration_fee_rate >= 0 "
            "AND administration_fee_rate <= 1",
            name="ck_cycle_annual_snapshots_nonnegative",
        ),
        sa.CheckConstraint(
            "gross_realized_result = administration_fee + distributable_result",
            name="ck_cycle_annual_snapshots_result_equation",
        ),
    )
    op.create_index(
        "ix_cycle_annual_snapshots_cycle_created",
        "cycle_annual_closing_snapshots",
        ["cycle_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "cycle_realized_gain_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cycle_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("realized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_type", sa.String(length=60), nullable=False),
        sa.Column("source_id", sa.String(length=150), nullable=False),
        sa.Column("idempotency_key", sa.String(length=150), nullable=False),
        sa.Column("evidence_reference", sa.String(length=500), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("reversal_of_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["cycle_id"], ["cycles.id"], name="fk_cycle_realized_gain_events_cycle", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_cycle_realized_gain_events_created_by", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reversal_of_id"], ["cycle_realized_gain_events.id"], name="fk_cycle_realized_gain_events_reversal", ondelete="RESTRICT"),
        sa.CheckConstraint(
            "event_type IN ('INVESTMENT_YIELD_REALIZED', 'OTHER_REALIZED_GAIN')",
            name="ck_cycle_realized_gain_events_type",
        ),
        sa.CheckConstraint("amount > 0", name="ck_cycle_realized_gain_events_positive"),
        sa.CheckConstraint("length(trim(source_type)) > 0 AND length(trim(source_id)) > 0", name="ck_cycle_realized_gain_events_source"),
        sa.CheckConstraint(
            "source_type IN ('BANK_STATEMENT', 'BANK_CORRECTION')",
            name="ck_cycle_realized_gain_events_external_source_only",
        ),
        sa.CheckConstraint("length(trim(idempotency_key)) > 0", name="ck_cycle_realized_gain_events_idempotency"),
        sa.CheckConstraint("length(trim(evidence_reference)) > 0", name="ck_cycle_realized_gain_events_evidence"),
        sa.CheckConstraint("length(evidence_hash) = 64", name="ck_cycle_realized_gain_events_evidence_hash"),
        sa.UniqueConstraint("idempotency_key", name="uq_cycle_realized_gain_events_idempotency"),
        sa.UniqueConstraint("cycle_id", "event_type", "source_type", "source_id", name="uq_cycle_realized_gain_events_source"),
        sa.UniqueConstraint("reversal_of_id", name="uq_cycle_realized_gain_events_one_full_reversal"),
    )
    op.create_index(
        "ix_cycle_realized_gain_events_cycle_realized",
        "cycle_realized_gain_events",
        ["cycle_id", "realized_at"],
        unique=False,
    )

    if dialect == "sqlite":
        _create_sqlite_guards(bind)
    elif dialect == "postgresql":
        _create_postgresql_guards(bind)
    else:
        raise RuntimeError(f"Unsupported database dialect for immutable financial guards: {dialect}")


def downgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "sqlite":
        for name in (
            "trg_cycle_annual_snapshots_no_update",
            "trg_cycle_annual_snapshots_no_delete",
            "trg_cycle_gain_events_no_update",
            "trg_cycle_gain_events_no_delete",
            "trg_cycle_gain_events_full_reversal",
        ):
            bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
    elif dialect == "postgresql":
        bind.exec_driver_sql("DROP TRIGGER IF EXISTS trg_cycle_annual_snapshots_immutable ON cycle_annual_closing_snapshots")
        bind.exec_driver_sql("DROP TRIGGER IF EXISTS trg_cycle_gain_events_immutable ON cycle_realized_gain_events")
        bind.exec_driver_sql("DROP TRIGGER IF EXISTS trg_cycle_gain_events_full_reversal ON cycle_realized_gain_events")
        bind.exec_driver_sql("DROP FUNCTION IF EXISTS cycle_annual_snapshot_immutable_guard()")
        bind.exec_driver_sql("DROP FUNCTION IF EXISTS cycle_gain_event_immutable_guard()")
        bind.exec_driver_sql("DROP FUNCTION IF EXISTS cycle_gain_event_full_reversal_guard()")

    op.drop_index("ix_cycle_realized_gain_events_cycle_realized", table_name="cycle_realized_gain_events")
    op.drop_table("cycle_realized_gain_events")
    op.drop_index("ix_cycle_annual_snapshots_cycle_created", table_name="cycle_annual_closing_snapshots")
    op.drop_table("cycle_annual_closing_snapshots")
    op.drop_index("ix_cycle_annual_closings_status", table_name="cycle_annual_closings")
    op.drop_table("cycle_annual_closings")

    op.drop_index("ix_loans_cycle_id", table_name="loans")
    if dialect == "sqlite":
        with op.batch_alter_table("loans", recreate="always") as batch_op:
            batch_op.drop_constraint("fk_loans_member_cycle_participation", type_="foreignkey")
            batch_op.drop_column("cycle_id")
    else:
        op.drop_constraint("fk_loans_member_cycle_participation", "loans", type_="foreignkey")
        op.drop_column("loans", "cycle_id")
