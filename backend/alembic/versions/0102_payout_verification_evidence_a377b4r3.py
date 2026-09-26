"""Payout destination verification attempt/evidence foundation.

This revision records verification attempts and sanitized evidence only. It
does not open any destination lifecycle transition.
"""

from alembic import op
import sqlalchemy as sa


revision = "0102_payout_verification_evidence_a377b4r3"
down_revision = "0101_member_payout_destination_a377b4r2"
branch_labels = None
depends_on = None


ATTEMPT_TABLE = "payout_destination_verification_attempts"
EVIDENCE_TABLE = "payout_destination_verification_evidence"
ACTIVE_INDEX = "uq_pdva_one_active_destination"
FINAL_INDEX = "uq_pdve_one_final_per_attempt"


def _create_sqlite_guards(bind):
    bind.exec_driver_sql(f"""
        CREATE TRIGGER trg_pdva_insert_guard
        BEFORE INSERT ON {ATTEMPT_TABLE}
        WHEN NEW.state <> 'REQUESTED' OR NOT EXISTS (
            SELECT 1 FROM member_payout_destinations AS destination
            WHERE destination.id = NEW.destination_id
              AND destination.version = NEW.destination_version
              AND destination.key_type = NEW.key_type
              AND destination.verification_status = 'UNVERIFIED'
        )
        BEGIN
            SELECT RAISE(ABORT, 'verification attempt destination binding is invalid');
        END
    """)
    bind.exec_driver_sql(f"""
        CREATE TRIGGER trg_pdva_update_guard
        BEFORE UPDATE ON {ATTEMPT_TABLE}
        WHEN NEW.id IS NOT OLD.id
          OR NEW.attempt_id IS NOT OLD.attempt_id
          OR NEW.idempotency_key IS NOT OLD.idempotency_key
          OR NEW.destination_id IS NOT OLD.destination_id
          OR NEW.destination_version IS NOT OLD.destination_version
          OR NEW.key_type IS NOT OLD.key_type
          OR NEW.provider_name IS NOT OLD.provider_name
          OR NEW.requested_at IS NOT OLD.requested_at
          OR NEW.requested_by IS NOT OLD.requested_by
          OR NEW.created_at IS NOT OLD.created_at
          OR NOT (
              NEW.state IS OLD.state
              OR (OLD.state = 'REQUESTED' AND NEW.state IN ('PENDING', 'FINAL'))
              OR (OLD.state = 'PENDING' AND NEW.state = 'FINAL')
          )
          OR (
              NEW.state IS NOT OLD.state AND NEW.state = 'PENDING'
              AND NOT EXISTS (
                  SELECT 1 FROM {EVIDENCE_TABLE} AS evidence
                  WHERE evidence.verification_attempt_id = OLD.id
                    AND evidence.result_state = 'PENDING'
              )
          )
          OR (
              NEW.state IS NOT OLD.state AND NEW.state = 'FINAL'
              AND NOT EXISTS (
                  SELECT 1 FROM {EVIDENCE_TABLE} AS evidence
                  WHERE evidence.verification_attempt_id = OLD.id
                    AND evidence.result_state = 'FINAL'
              )
          )
        BEGIN
            SELECT RAISE(ABORT, 'verification attempt identity or state transition is invalid');
        END
    """)
    bind.exec_driver_sql(f"""
        CREATE TRIGGER trg_pdva_delete_guard
        BEFORE DELETE ON {ATTEMPT_TABLE}
        BEGIN
            SELECT RAISE(ABORT, 'verification attempts cannot be deleted');
        END
    """)
    bind.exec_driver_sql(f"""
        CREATE TRIGGER trg_pdve_insert_guard
        BEFORE INSERT ON {EVIDENCE_TABLE}
        WHEN NOT EXISTS (
            SELECT 1 FROM {ATTEMPT_TABLE} AS attempt
            WHERE attempt.id = NEW.verification_attempt_id
              AND attempt.state IN ('REQUESTED', 'PENDING')
        ) OR EXISTS (
            SELECT 1 FROM {EVIDENCE_TABLE} AS evidence
            WHERE evidence.verification_attempt_id = NEW.verification_attempt_id
              AND evidence.result_state = 'FINAL'
        )
        BEGIN
            SELECT RAISE(ABORT, 'verification evidence attempt is closed');
        END
    """)
    bind.exec_driver_sql(f"""
        CREATE TRIGGER trg_pdve_update_guard
        BEFORE UPDATE ON {EVIDENCE_TABLE}
        BEGIN
            SELECT RAISE(ABORT, 'verification evidence is append-only');
        END
    """)
    bind.exec_driver_sql(f"""
        CREATE TRIGGER trg_pdve_delete_guard
        BEFORE DELETE ON {EVIDENCE_TABLE}
        BEGIN
            SELECT RAISE(ABORT, 'verification evidence cannot be deleted');
        END
    """)


def _create_postgresql_guards(bind):
    bind.exec_driver_sql(f"""
        CREATE FUNCTION pdva_guard()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.state <> 'REQUESTED' OR NOT EXISTS (
                    SELECT 1 FROM member_payout_destinations AS destination
                    WHERE destination.id = NEW.destination_id
                      AND destination.version = NEW.destination_version
                      AND destination.key_type = NEW.key_type
                      AND destination.verification_status = 'UNVERIFIED'
                ) THEN
                    RAISE EXCEPTION 'verification attempt destination binding is invalid';
                END IF;
                RETURN NEW;
            END IF;

            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'verification attempts cannot be deleted';
            END IF;

            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.attempt_id IS DISTINCT FROM OLD.attempt_id
               OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
               OR NEW.destination_id IS DISTINCT FROM OLD.destination_id
               OR NEW.destination_version IS DISTINCT FROM OLD.destination_version
               OR NEW.key_type IS DISTINCT FROM OLD.key_type
               OR NEW.provider_name IS DISTINCT FROM OLD.provider_name
               OR NEW.requested_at IS DISTINCT FROM OLD.requested_at
               OR NEW.requested_by IS DISTINCT FROM OLD.requested_by
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'verification attempt identity is immutable';
            END IF;

            IF NEW.state IS NOT DISTINCT FROM OLD.state THEN
                RETURN NEW;
            END IF;

            IF NOT (
                (OLD.state = 'REQUESTED' AND NEW.state IN ('PENDING', 'FINAL'))
                OR (OLD.state = 'PENDING' AND NEW.state = 'FINAL')
            ) THEN
                RAISE EXCEPTION 'verification attempt state transition is invalid';
            END IF;

            IF NEW.state = 'PENDING' AND NOT EXISTS (
                SELECT 1 FROM {EVIDENCE_TABLE} AS evidence
                WHERE evidence.verification_attempt_id = OLD.id
                  AND evidence.result_state = 'PENDING'
            ) THEN
                RAISE EXCEPTION 'PENDING state requires pending evidence';
            END IF;
            IF NEW.state = 'FINAL' AND NOT EXISTS (
                SELECT 1 FROM {EVIDENCE_TABLE} AS evidence
                WHERE evidence.verification_attempt_id = OLD.id
                  AND evidence.result_state = 'FINAL'
            ) THEN
                RAISE EXCEPTION 'FINAL state requires final evidence';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    bind.exec_driver_sql(f"""
        CREATE TRIGGER trg_pdva_guard
        BEFORE INSERT OR UPDATE OR DELETE ON {ATTEMPT_TABLE}
        FOR EACH ROW EXECUTE FUNCTION pdva_guard()
    """)
    bind.exec_driver_sql(f"""
        CREATE FUNCTION pdve_guard()
        RETURNS trigger AS $$
        DECLARE attempt_state VARCHAR(20);
        BEGIN
            IF TG_OP = 'INSERT' THEN
                SELECT attempt.state INTO attempt_state
                FROM {ATTEMPT_TABLE} AS attempt
                WHERE attempt.id = NEW.verification_attempt_id
                FOR UPDATE;
                IF NOT FOUND OR attempt_state NOT IN ('REQUESTED', 'PENDING') THEN
                    RAISE EXCEPTION 'verification evidence attempt is not open';
                END IF;
                IF EXISTS (
                    SELECT 1 FROM {EVIDENCE_TABLE} AS evidence
                    WHERE evidence.verification_attempt_id = NEW.verification_attempt_id
                      AND evidence.result_state = 'FINAL'
                ) THEN
                    RAISE EXCEPTION 'verification evidence cannot follow final evidence';
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'verification evidence is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    bind.exec_driver_sql(f"""
        CREATE TRIGGER trg_pdve_guard
        BEFORE INSERT OR UPDATE OR DELETE ON {EVIDENCE_TABLE}
        FOR EACH ROW EXECUTE FUNCTION pdve_guard()
    """)


def upgrade():
    op.create_table(
        ATTEMPT_TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("attempt_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=150), nullable=False),
        sa.Column("destination_id", sa.Integer(), nullable=False),
        sa.Column("destination_version", sa.Integer(), nullable=False),
        sa.Column("key_type", sa.String(length=10), nullable=False),
        sa.Column("provider_name", sa.String(length=80), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False, server_default=sa.text("'REQUESTED'")),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.ForeignKeyConstraint(["destination_id"], ["member_payout_destinations.id"], name="fk_pdva_destination", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], name="fk_pdva_requested_by", ondelete="RESTRICT"),
        sa.UniqueConstraint("attempt_id", name="uq_pdva_attempt_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_pdva_idempotency_key"),
        sa.CheckConstraint("length(trim(attempt_id)) > 0", name="ck_pdva_attempt_id_nonempty"),
        sa.CheckConstraint("length(trim(idempotency_key)) > 0", name="ck_pdva_idempotency_nonempty"),
        sa.CheckConstraint("destination_version >= 1", name="ck_pdva_destination_version_positive"),
        sa.CheckConstraint("key_type IN ('CPF', 'PHONE', 'EMAIL', 'EVP')", name="ck_pdva_key_type"),
        sa.CheckConstraint("length(trim(provider_name)) > 0", name="ck_pdva_provider_nonempty"),
        sa.CheckConstraint("state IN ('REQUESTED', 'PENDING', 'FINAL')", name="ck_pdva_state"),
    )
    op.create_index(
        ACTIVE_INDEX, ATTEMPT_TABLE, ["destination_id"], unique=True,
        sqlite_where=sa.text("state IN ('REQUESTED', 'PENDING')"),
        postgresql_where=sa.text("state IN ('REQUESTED', 'PENDING')"),
    )
    op.create_table(
        EVIDENCE_TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("verification_attempt_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("result_state", sa.String(length=20), nullable=False),
        sa.Column("outcome", sa.String(length=30), nullable=True),
        sa.Column("reason_code", sa.String(length=100), nullable=True),
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
        sa.Column("provider_response_id", sa.String(length=255), nullable=True),
        sa.Column("provider_event_id", sa.String(length=255), nullable=True),
        sa.Column("provider_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authenticity_status", sa.String(length=20), nullable=False),
        sa.Column("authenticity_method", sa.String(length=80), nullable=True),
        sa.Column("authenticity_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("freshness", sa.String(length=20), nullable=False),
        sa.Column("evidence_digest", sa.String(length=71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.ForeignKeyConstraint(["verification_attempt_id"], [f"{ATTEMPT_TABLE}.id"], name="fk_pdve_attempt", ondelete="RESTRICT"),
        sa.UniqueConstraint("verification_attempt_id", "sequence", name="uq_pdve_attempt_sequence"),
        sa.UniqueConstraint("verification_attempt_id", "evidence_digest", name="uq_pdve_attempt_digest"),
        sa.CheckConstraint("sequence >= 1", name="ck_pdve_sequence_positive"),
        sa.CheckConstraint(
            "(result_state = 'PENDING' AND outcome IS NULL) OR "
            "(result_state = 'FINAL' AND outcome IS NOT NULL AND outcome IN "
            "('CONFIRMED', 'NOT_CONFIRMED', 'RETRYABLE', 'AMBIGUOUS', 'INVALID_RESPONSE'))",
            name="ck_pdve_state_outcome",
        ),
        sa.CheckConstraint(
            "authenticity_status IN ('NOT_CHECKED', 'AUTHENTIC', 'INVALID', 'UNVERIFIABLE')",
            name="ck_pdve_authenticity_status",
        ),
        sa.CheckConstraint(
            "(authenticity_status = 'NOT_CHECKED' AND authenticity_method IS NULL AND authenticity_checked_at IS NULL) OR "
            "(authenticity_status <> 'NOT_CHECKED' AND authenticity_method IS NOT NULL "
            "AND length(trim(authenticity_method)) > 0 AND authenticity_checked_at IS NOT NULL)",
            name="ck_pdve_authenticity_fields",
        ),
        sa.CheckConstraint("freshness IN ('CURRENT', 'STALE', 'REVOKED', 'NOT_UNVERIFIED')", name="ck_pdve_freshness"),
        sa.CheckConstraint(
            "length(evidence_digest) = 71 AND substr(evidence_digest, 1, 7) = 'sha256:'",
            name="ck_pdve_digest_prefix_length",
        ),
    )
    op.create_index(
        FINAL_INDEX, EVIDENCE_TABLE, ["verification_attempt_id"], unique=True,
        sqlite_where=sa.text("result_state = 'FINAL'"),
        postgresql_where=sa.text("result_state = 'FINAL'"),
    )
    if op.get_bind().dialect.name == "sqlite":
        _create_sqlite_guards(op.get_bind())
    elif op.get_bind().dialect.name == "postgresql":
        _create_postgresql_guards(op.get_bind())
    else:
        raise RuntimeError("Unsupported dialect for payout verification evidence guards")


def downgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "sqlite":
        for name in (
            "trg_pdva_insert_guard", "trg_pdva_update_guard", "trg_pdva_delete_guard",
            "trg_pdve_insert_guard", "trg_pdve_update_guard", "trg_pdve_delete_guard",
        ):
            bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
    elif dialect == "postgresql":
        bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS trg_pdva_guard ON {ATTEMPT_TABLE}")
        bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS trg_pdve_guard ON {EVIDENCE_TABLE}")
        bind.exec_driver_sql("DROP FUNCTION IF EXISTS pdva_guard()")
        bind.exec_driver_sql("DROP FUNCTION IF EXISTS pdve_guard()")
    else:
        raise RuntimeError("Unsupported dialect for payout verification evidence downgrade")
    op.drop_index(FINAL_INDEX, table_name=EVIDENCE_TABLE)
    op.drop_table(EVIDENCE_TABLE)
    op.drop_index(ACTIVE_INDEX, table_name=ATTEMPT_TABLE)
    op.drop_table(ATTEMPT_TABLE)
