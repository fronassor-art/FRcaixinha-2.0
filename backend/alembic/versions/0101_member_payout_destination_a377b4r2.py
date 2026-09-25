"""Versioned encrypted PIX destinations for members (A3.77B4-R2)."""

from alembic import op
import sqlalchemy as sa


revision = "0101_member_payout_destination_a377b4r2"
down_revision = "0100_cycle_payout_obligations_a377b4r1"
branch_labels = None
depends_on = None


def _create_sqlite_guards(bind):
    bind.exec_driver_sql("""
        CREATE TRIGGER trg_mpd_insert_guard
        BEFORE INSERT ON member_payout_destinations
        WHEN NEW.verification_status <> 'UNVERIFIED'
          OR NEW.verified_at IS NOT NULL OR NEW.verified_by IS NOT NULL
          OR NEW.revoked_at IS NOT NULL OR NEW.revoked_by IS NOT NULL
        BEGIN
            SELECT RAISE(ABORT, 'new payout destination must be UNVERIFIED');
        END
    """)
    bind.exec_driver_sql("""
        CREATE TRIGGER trg_mpd_update_guard
        BEFORE UPDATE ON member_payout_destinations
        WHEN NEW.id IS NOT OLD.id
          OR NEW.member_id IS NOT OLD.member_id
          OR NEW.version IS NOT OLD.version
          OR NEW.key_type IS NOT OLD.key_type
          OR NEW.encrypted_value IS NOT OLD.encrypted_value
          OR NEW.masked_value IS NOT OLD.masked_value
          OR NEW.created_at IS NOT OLD.created_at
          OR NEW.created_by IS NOT OLD.created_by
          OR NOT (
              (NEW.verification_status IS OLD.verification_status
               AND NEW.verified_at IS OLD.verified_at
               AND NEW.verified_by IS OLD.verified_by
               AND NEW.revoked_at IS OLD.revoked_at
               AND NEW.revoked_by IS OLD.revoked_by)
              OR
              (OLD.verification_status IN ('UNVERIFIED', 'VERIFIED')
               AND NEW.verification_status = 'REVOKED'
               AND NEW.verified_at IS OLD.verified_at
               AND NEW.verified_by IS OLD.verified_by
               AND NEW.revoked_at IS NOT NULL
               AND NEW.revoked_by IS NOT NULL
               AND OLD.revoked_at IS NULL AND OLD.revoked_by IS NULL)
          )
        BEGIN
            SELECT RAISE(ABORT, 'payout destination version or lifecycle is immutable');
        END
    """)
    bind.exec_driver_sql("""
        CREATE TRIGGER trg_mpd_delete_guard
        BEFORE DELETE ON member_payout_destinations
        BEGIN
            SELECT RAISE(ABORT, 'payout destination history cannot be deleted');
        END
    """)


def _create_postgresql_guards(bind):
    bind.exec_driver_sql("""
        CREATE FUNCTION member_payout_destination_guard()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.verification_status <> 'UNVERIFIED'
                   OR NEW.verified_at IS NOT NULL OR NEW.verified_by IS NOT NULL
                   OR NEW.revoked_at IS NOT NULL OR NEW.revoked_by IS NOT NULL THEN
                    RAISE EXCEPTION 'new payout destination must be UNVERIFIED';
                END IF;
                RETURN NEW;
            END IF;

            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'payout destination history cannot be deleted';
            END IF;

            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.member_id IS DISTINCT FROM OLD.member_id
               OR NEW.version IS DISTINCT FROM OLD.version
               OR NEW.key_type IS DISTINCT FROM OLD.key_type
               OR NEW.encrypted_value IS DISTINCT FROM OLD.encrypted_value
               OR NEW.masked_value IS DISTINCT FROM OLD.masked_value
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
               OR NEW.created_by IS DISTINCT FROM OLD.created_by THEN
                RAISE EXCEPTION 'payout destination version fields are immutable';
            END IF;

            IF NEW.verification_status IS NOT DISTINCT FROM OLD.verification_status
               AND NEW.verified_at IS NOT DISTINCT FROM OLD.verified_at
               AND NEW.verified_by IS NOT DISTINCT FROM OLD.verified_by
               AND NEW.revoked_at IS NOT DISTINCT FROM OLD.revoked_at
               AND NEW.revoked_by IS NOT DISTINCT FROM OLD.revoked_by THEN
                RETURN NEW;
            END IF;

            IF OLD.verification_status IN ('UNVERIFIED', 'VERIFIED')
               AND NEW.verification_status = 'REVOKED'
               AND NEW.verified_at IS NOT DISTINCT FROM OLD.verified_at
               AND NEW.verified_by IS NOT DISTINCT FROM OLD.verified_by
               AND OLD.revoked_at IS NULL AND OLD.revoked_by IS NULL
               AND NEW.revoked_at IS NOT NULL AND NEW.revoked_by IS NOT NULL THEN
                RETURN NEW;
            END IF;

            RAISE EXCEPTION 'payout destination lifecycle transition is not allowed';
        END;
        $$ LANGUAGE plpgsql
    """)
    bind.exec_driver_sql("""
        CREATE TRIGGER trg_mpd_guard
        BEFORE INSERT OR UPDATE OR DELETE ON member_payout_destinations
        FOR EACH ROW EXECUTE FUNCTION member_payout_destination_guard()
    """)


def upgrade():
    op.create_table(
        "member_payout_destinations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("key_type", sa.String(length=10), nullable=False),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("masked_value", sa.String(length=255), nullable=False),
        sa.Column(
            "verification_status", sa.String(length=20), nullable=False,
            server_default=sa.text("'UNVERIFIED'"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by", sa.Integer(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name="fk_mpd_member", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_mpd_created_by", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by"], ["users.id"], name="fk_mpd_verified_by", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"], ["users.id"], name="fk_mpd_revoked_by", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("member_id", "version", name="uq_mpd_member_version"),
        sa.CheckConstraint("version >= 1", name="ck_mpd_version_positive"),
        sa.CheckConstraint(
            "key_type IN ('CPF', 'PHONE', 'EMAIL', 'EVP')", name="ck_mpd_key_type",
        ),
        sa.CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'VERIFIED', 'REVOKED')",
            name="ck_mpd_verification_status",
        ),
        sa.CheckConstraint(
            "(verification_status = 'UNVERIFIED' AND verified_at IS NULL AND verified_by IS NULL "
            "AND revoked_at IS NULL AND revoked_by IS NULL) OR "
            "(verification_status = 'VERIFIED' AND verified_at IS NOT NULL AND verified_by IS NOT NULL "
            "AND revoked_at IS NULL AND revoked_by IS NULL) OR "
            "(verification_status = 'REVOKED' AND revoked_at IS NOT NULL AND revoked_by IS NOT NULL "
            "AND ((verified_at IS NULL AND verified_by IS NULL) OR "
            "(verified_at IS NOT NULL AND verified_by IS NOT NULL)))",
            name="ck_mpd_lifecycle_fields",
        ),
    )
    op.create_index(
        "uq_mpd_one_active_member", "member_payout_destinations", ["member_id"],
        unique=True,
        postgresql_where=sa.text("verification_status <> 'REVOKED'"),
        sqlite_where=sa.text("verification_status <> 'REVOKED'"),
    )

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _create_sqlite_guards(bind)
    elif bind.dialect.name == "postgresql":
        _create_postgresql_guards(bind)
    else:
        raise RuntimeError("Unsupported database dialect for payout destination guards")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql("DROP TRIGGER IF EXISTS trg_mpd_insert_guard")
        bind.exec_driver_sql("DROP TRIGGER IF EXISTS trg_mpd_update_guard")
        bind.exec_driver_sql("DROP TRIGGER IF EXISTS trg_mpd_delete_guard")
    elif bind.dialect.name == "postgresql":
        bind.exec_driver_sql("DROP TRIGGER IF EXISTS trg_mpd_guard ON member_payout_destinations")
        bind.exec_driver_sql("DROP FUNCTION IF EXISTS member_payout_destination_guard()")
    else:
        raise RuntimeError("Unsupported database dialect for payout destination downgrade")
    op.drop_table("member_payout_destinations")
