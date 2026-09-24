"""A3.77B3 R1R2: immutable cash evidence, reviews and approved review link."""

from alembic import op
import sqlalchemy as sa


revision = "0099_cycle_closing_review_a377b3r1"
down_revision = "0098_cycle_closing_persistence_a377b2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cycle_annual_closing_cash_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("closing_id", sa.Integer(), nullable=False),
        sa.Column("cycle_id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Integer(), nullable=False),
        sa.Column("storage_reference", sa.String(180), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("declared_cash_balance", sa.Numeric(14, 2), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closing_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), nullable=False),
        sa.Column("attested_by", sa.Integer(), nullable=False),
        sa.Column("attested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.ForeignKeyConstraint(["closing_id", "cycle_id"], ["cycle_annual_closings.id", "cycle_annual_closings.cycle_id"], name="fk_cycle_cash_evidence_closing_cycle", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["file_id"], ["workflow_execution_evidence_files.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["attested_by"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("id", "closing_id", "cycle_id", name="uq_cycle_cash_evidence_id_closing_cycle"),
        sa.CheckConstraint("declared_cash_balance >= 0", name="ck_cycle_cash_evidence_balance"),
        sa.CheckConstraint("length(file_sha256) = 64", name="ck_cycle_cash_evidence_hash"),
    )
    op.create_table(
        "cycle_annual_closing_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("closing_id", sa.Integer(), nullable=False),
        sa.Column("cycle_id", sa.Integer(), nullable=False),
        sa.Column("cash_evidence_id", sa.Integer(), nullable=False),
        sa.Column("review_version", sa.Integer(), nullable=False),
        sa.Column("process_revision", sa.Integer(), nullable=False),
        sa.Column("closing_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calculation_version", sa.String(length=60), nullable=False),
        sa.Column("calculation_hash", sa.String(length=64), nullable=False),
        sa.Column("ledger_cash_balance", sa.Numeric(14, 2), nullable=False),
        sa.Column("actual_cash_balance", sa.Numeric(14, 2), nullable=False),
        sa.Column("reconciliation_difference", sa.Numeric(14, 2), nullable=False),
        sa.Column("participant_payout_liability", sa.Numeric(14, 2), nullable=False),
        sa.Column("administration_fee", sa.Numeric(14, 2), nullable=False),
        sa.Column("required_liquidity", sa.Numeric(14, 2), nullable=False),
        sa.Column("liquidity_surplus", sa.Numeric(14, 2), nullable=False),
        sa.Column("cash_evidence_reference", sa.String(length=500), nullable=False),
        sa.Column("cash_evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("reconciliation_payload", sa.Text(), nullable=False),
        sa.Column("reconciliation_hash", sa.String(length=64), nullable=False),
        sa.Column("review_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["closing_id", "cycle_id"],
            ["cycle_annual_closings.id", "cycle_annual_closings.cycle_id"],
            name="fk_cycle_annual_reviews_closing_cycle", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cash_evidence_id", "closing_id", "cycle_id"],
            ["cycle_annual_closing_cash_evidence.id", "cycle_annual_closing_cash_evidence.closing_id", "cycle_annual_closing_cash_evidence.cycle_id"],
            name="fk_cycle_annual_reviews_cash_evidence", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["cycle_id"], ["cycles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("closing_id", "review_version", name="uq_cycle_annual_reviews_version"),
        sa.UniqueConstraint("id", "closing_id", name="uq_cycle_annual_reviews_id_closing"),
        sa.UniqueConstraint("closing_id", "review_hash", name="uq_cycle_annual_reviews_hash"),
        sa.CheckConstraint("review_version >= 1 AND process_revision >= 1", name="ck_cycle_annual_reviews_versions"),
        sa.CheckConstraint("actual_cash_balance >= 0 AND reconciliation_difference = 0 AND liquidity_surplus >= 0", name="ck_cycle_annual_reviews_gates"),
        sa.CheckConstraint("required_liquidity = participant_payout_liability + administration_fee", name="ck_cycle_annual_reviews_liquidity_equation"),
        sa.CheckConstraint("length(calculation_hash) = 64 AND length(cash_evidence_hash) = 64 AND length(reconciliation_hash) = 64 AND length(review_hash) = 64", name="ck_cycle_annual_reviews_hash_lengths"),
    )
    op.create_index(
        "ix_cycle_annual_reviews_closing_created", "cycle_annual_closing_reviews",
        ["closing_id", "created_at"], unique=False,
    )
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite supports ADD COLUMN with an inline REFERENCES constraint when
        # the new column is nullable and has no non-NULL default. Alembic's
        # generic add_column path emits a separate ALTER CONSTRAINT, which
        # SQLite does not support.
        bind.exec_driver_sql(
            "ALTER TABLE cycle_annual_closings ADD COLUMN approved_review_id INTEGER "
            "CONSTRAINT fk_cycle_annual_closings_approved_review "
            "REFERENCES cycle_annual_closing_reviews(id) ON DELETE RESTRICT"
        )
        bind.exec_driver_sql("""
            CREATE TRIGGER trg_cycle_annual_approved_review_insert
            BEFORE INSERT ON cycle_annual_closings
            WHEN NEW.approved_review_id IS NOT NULL
            BEGIN SELECT RAISE(ABORT, 'approved review cannot be set at creation'); END
        """)
        bind.exec_driver_sql("""
            CREATE TRIGGER trg_cycle_annual_approved_review_link
            BEFORE UPDATE OF approved_review_id ON cycle_annual_closings
            BEGIN
                SELECT CASE WHEN OLD.approved_review_id IS NOT NULL
                    AND NEW.approved_review_id IS NOT OLD.approved_review_id
                    THEN RAISE(ABORT, 'approved review link is final') END;
                SELECT CASE WHEN NEW.approved_review_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM cycle_annual_closing_reviews AS review
                    WHERE review.id = NEW.approved_review_id
                      AND review.closing_id = NEW.id AND review.cycle_id = NEW.cycle_id
                ) THEN RAISE(ABORT, 'approved review must belong to annual closing') END;
            END
        """)
        bind.exec_driver_sql("""
            CREATE TRIGGER trg_cycle_annual_reviews_no_update
            BEFORE UPDATE ON cycle_annual_closing_reviews
            BEGIN SELECT RAISE(ABORT, 'annual closing reviews are immutable'); END
        """)
        bind.exec_driver_sql("""
            CREATE TRIGGER trg_cycle_annual_reviews_no_delete
            BEFORE DELETE ON cycle_annual_closing_reviews
            BEGIN SELECT RAISE(ABORT, 'annual closing reviews are immutable'); END
        """)
        bind.exec_driver_sql("""
            CREATE TRIGGER trg_cycle_cash_evidence_no_update
            BEFORE UPDATE ON cycle_annual_closing_cash_evidence
            BEGIN SELECT RAISE(ABORT, 'annual closing cash evidence is immutable'); END
        """)
        bind.exec_driver_sql("""
            CREATE TRIGGER trg_cycle_cash_evidence_no_delete
            BEFORE DELETE ON cycle_annual_closing_cash_evidence
            BEGIN SELECT RAISE(ABORT, 'annual closing cash evidence is immutable'); END
        """)
    elif bind.dialect.name == "postgresql":
        op.add_column(
            "cycle_annual_closings",
            sa.Column("approved_review_id", sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            "fk_cycle_annual_closings_approved_review",
            "cycle_annual_closings",
            "cycle_annual_closing_reviews",
            ["approved_review_id"], ["id"], ondelete="RESTRICT",
        )
        bind.exec_driver_sql("""
            CREATE FUNCTION cycle_annual_approved_review_link_guard()
            RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'INSERT' AND NEW.approved_review_id IS NOT NULL THEN
                    RAISE EXCEPTION 'approved review cannot be set at creation';
                END IF;
                IF TG_OP = 'UPDATE' THEN
                    IF OLD.approved_review_id IS NOT NULL
                       AND NEW.approved_review_id IS DISTINCT FROM OLD.approved_review_id THEN
                        RAISE EXCEPTION 'approved review link is final';
                    END IF;
                END IF;
                IF NEW.approved_review_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM cycle_annual_closing_reviews AS review
                    WHERE review.id = NEW.approved_review_id
                      AND review.closing_id = NEW.id AND review.cycle_id = NEW.cycle_id
                ) THEN
                    RAISE EXCEPTION 'approved review must belong to annual closing';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)
        bind.exec_driver_sql("""
            CREATE TRIGGER trg_cycle_annual_approved_review_link
            BEFORE INSERT OR UPDATE ON cycle_annual_closings
            FOR EACH ROW EXECUTE FUNCTION cycle_annual_approved_review_link_guard()
        """)
        bind.exec_driver_sql("""
            CREATE FUNCTION cycle_annual_review_immutable_guard()
            RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'annual closing reviews are immutable'; END;
            $$ LANGUAGE plpgsql
        """)
        bind.exec_driver_sql("""
            CREATE TRIGGER trg_cycle_annual_reviews_immutable
            BEFORE UPDATE OR DELETE ON cycle_annual_closing_reviews
            FOR EACH ROW EXECUTE FUNCTION cycle_annual_review_immutable_guard()
        """)
        bind.exec_driver_sql("""
            CREATE FUNCTION cycle_annual_cash_evidence_immutable_guard()
            RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'annual closing cash evidence is immutable'; END;
            $$ LANGUAGE plpgsql
        """)
        bind.exec_driver_sql("""
            CREATE TRIGGER trg_cycle_annual_cash_evidence_immutable
            BEFORE UPDATE OR DELETE ON cycle_annual_closing_cash_evidence
            FOR EACH ROW EXECUTE FUNCTION cycle_annual_cash_evidence_immutable_guard()
        """)
    else:
        raise RuntimeError("Unsupported database dialect for immutable closing reviews")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql("DROP TRIGGER IF EXISTS trg_cycle_annual_approved_review_insert")
        bind.exec_driver_sql("DROP TRIGGER IF EXISTS trg_cycle_annual_approved_review_link")
        bind.exec_driver_sql("DROP TRIGGER IF EXISTS trg_cycle_annual_reviews_no_update")
        bind.exec_driver_sql("DROP TRIGGER IF EXISTS trg_cycle_annual_reviews_no_delete")
        bind.exec_driver_sql("DROP TRIGGER IF EXISTS trg_cycle_cash_evidence_no_update")
        bind.exec_driver_sql("DROP TRIGGER IF EXISTS trg_cycle_cash_evidence_no_delete")
        # SQLite 3.35+ supports native DROP COLUMN. Keeping this native avoids
        # rebuilding cycle_annual_closings while its composite key is referenced
        # by the 0098 snapshot and 0099 evidence/review tables.
        bind.exec_driver_sql(
            "ALTER TABLE cycle_annual_closings DROP COLUMN approved_review_id"
        )
    elif bind.dialect.name == "postgresql":
        bind.exec_driver_sql("DROP TRIGGER IF EXISTS trg_cycle_annual_approved_review_link ON cycle_annual_closings")
        bind.exec_driver_sql("DROP FUNCTION IF EXISTS cycle_annual_approved_review_link_guard()")
        bind.exec_driver_sql("DROP TRIGGER IF EXISTS trg_cycle_annual_reviews_immutable ON cycle_annual_closing_reviews")
        bind.exec_driver_sql("DROP FUNCTION IF EXISTS cycle_annual_review_immutable_guard()")
        bind.exec_driver_sql("DROP TRIGGER IF EXISTS trg_cycle_annual_cash_evidence_immutable ON cycle_annual_closing_cash_evidence")
        bind.exec_driver_sql("DROP FUNCTION IF EXISTS cycle_annual_cash_evidence_immutable_guard()")
        op.drop_constraint(
            "fk_cycle_annual_closings_approved_review",
            "cycle_annual_closings", type_="foreignkey",
        )
        op.drop_column("cycle_annual_closings", "approved_review_id")
    else:
        raise RuntimeError("Unsupported database dialect for immutable closing reviews")

    op.drop_index("ix_cycle_annual_reviews_closing_created", table_name="cycle_annual_closing_reviews")
    op.drop_table("cycle_annual_closing_reviews")
    op.drop_table("cycle_annual_closing_cash_evidence")
