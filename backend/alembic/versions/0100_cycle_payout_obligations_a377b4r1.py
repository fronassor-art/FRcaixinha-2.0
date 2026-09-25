"""A3.77B4-R1 proposed: immutable annual closing payout obligations."""

from alembic import op
import sqlalchemy as sa


revision = "0100_cycle_payout_obligations_a377b4r1"
down_revision = "0099_cycle_closing_review_a377b3r1"
branch_labels = None
depends_on = None


def _create_sqlite_insert_guard(bind):
    bind.exec_driver_sql("""
        CREATE TRIGGER trg_cpo_insert_guard
        BEFORE INSERT ON cycle_annual_closing_payout_obligations
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1
                FROM cycle_annual_closing_snapshots AS snapshot
                JOIN cycle_annual_closings AS closing
                  ON closing.id = NEW.closing_id
                 AND closing.cycle_id = NEW.cycle_id
                WHERE snapshot.id = NEW.snapshot_id
                  AND snapshot.closing_id = NEW.closing_id
                  AND snapshot.cycle_id = NEW.cycle_id
                  AND snapshot.payload_hash = NEW.source_payload_hash
                  AND closing.status = 'CLOSED'
            ) THEN RAISE(ABORT, 'payout obligation snapshot linkage is invalid') END;

            SELECT CASE WHEN NOT EXISTS (
                SELECT 1
                FROM cycle_participations AS participation
                WHERE participation.id = NEW.cycle_participation_id
                  AND participation.member_id = NEW.member_id
                  AND participation.cycle_id = NEW.cycle_id
            ) THEN RAISE(ABORT, 'payout obligation participation linkage is invalid') END;
        END
    """)


def _create_sqlite_immutability_guards(bind):
    bind.exec_driver_sql("""
        CREATE TRIGGER trg_cpo_no_update
        BEFORE UPDATE ON cycle_annual_closing_payout_obligations
        BEGIN
            SELECT RAISE(ABORT, 'annual closing payout obligations are immutable');
        END
    """)
    bind.exec_driver_sql("""
        CREATE TRIGGER trg_cpo_no_delete
        BEFORE DELETE ON cycle_annual_closing_payout_obligations
        BEGIN
            SELECT RAISE(ABORT, 'annual closing payout obligations are immutable');
        END
    """)


def _create_postgresql_immutability_guards(bind):
    bind.exec_driver_sql("""
        CREATE FUNCTION cycle_annual_payout_obligation_immutable_guard()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'annual closing payout obligations are immutable';
        END;
        $$ LANGUAGE plpgsql
    """)
    bind.exec_driver_sql("""
        CREATE TRIGGER trg_cpo_immutable
        BEFORE UPDATE OR DELETE ON cycle_annual_closing_payout_obligations
        FOR EACH ROW EXECUTE FUNCTION cycle_annual_payout_obligation_immutable_guard()
    """)


def _create_postgresql_insert_guard(bind):
    bind.exec_driver_sql("""
        CREATE FUNCTION cycle_payout_obligation_insert_guard()
        RETURNS trigger AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM cycle_annual_closing_snapshots AS snapshot
                JOIN cycle_annual_closings AS closing
                  ON closing.id = NEW.closing_id
                 AND closing.cycle_id = NEW.cycle_id
                WHERE snapshot.id = NEW.snapshot_id
                  AND snapshot.closing_id = NEW.closing_id
                  AND snapshot.cycle_id = NEW.cycle_id
                  AND snapshot.payload_hash = NEW.source_payload_hash
                  AND closing.status = 'CLOSED'
            ) THEN
                RAISE EXCEPTION 'payout obligation snapshot linkage is invalid';
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM cycle_participations AS participation
                WHERE participation.id = NEW.cycle_participation_id
                  AND participation.member_id = NEW.member_id
                  AND participation.cycle_id = NEW.cycle_id
            ) THEN
                RAISE EXCEPTION 'payout obligation participation linkage is invalid';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    bind.exec_driver_sql("""
        CREATE TRIGGER trg_cpo_insert_guard
        BEFORE INSERT ON cycle_annual_closing_payout_obligations
        FOR EACH ROW EXECUTE FUNCTION cycle_payout_obligation_insert_guard()
    """)


def upgrade():
    op.create_table(
        "cycle_annual_closing_payout_obligations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("closing_id", sa.Integer(), nullable=False),
        sa.Column("cycle_id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("cycle_participation_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("source_payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["cycle_annual_closing_snapshots.id"],
            name="fk_cpo_snapshot", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["closing_id", "cycle_id"],
            ["cycle_annual_closings.id", "cycle_annual_closings.cycle_id"],
            name="fk_cpo_closing_cycle", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name="fk_cpo_member", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cycle_participation_id"], ["cycle_participations.id"],
            name="fk_cpo_participation", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("snapshot_id", "member_id", name="uq_cpo_snapshot_member"),
        sa.UniqueConstraint("snapshot_id", "cycle_participation_id", name="uq_cpo_snapshot_part"),
        sa.CheckConstraint("amount >= 0", name="ck_cpo_amount_nonnegative"),
        sa.CheckConstraint("length(source_payload_hash) = 64", name="ck_cpo_hash_length"),
    )

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _create_sqlite_insert_guard(bind)
        _create_sqlite_immutability_guards(bind)
    elif bind.dialect.name == "postgresql":
        _create_postgresql_insert_guard(bind)
        _create_postgresql_immutability_guards(bind)
    else:
        raise RuntimeError("Unsupported database dialect for payout obligation immutability")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql("DROP TRIGGER IF EXISTS trg_cpo_insert_guard")
        bind.exec_driver_sql("DROP TRIGGER IF EXISTS trg_cpo_no_update")
        bind.exec_driver_sql("DROP TRIGGER IF EXISTS trg_cpo_no_delete")
    elif bind.dialect.name == "postgresql":
        bind.exec_driver_sql(
            "DROP TRIGGER IF EXISTS trg_cpo_insert_guard ON cycle_annual_closing_payout_obligations"
        )
        bind.exec_driver_sql("DROP FUNCTION IF EXISTS cycle_payout_obligation_insert_guard()")
        bind.exec_driver_sql(
            "DROP TRIGGER IF EXISTS trg_cpo_immutable ON cycle_annual_closing_payout_obligations"
        )
        bind.exec_driver_sql("DROP FUNCTION IF EXISTS cycle_annual_payout_obligation_immutable_guard()")
    else:
        raise RuntimeError("Unsupported database dialect for payout obligation immutability")
    op.drop_table("cycle_annual_closing_payout_obligations")
