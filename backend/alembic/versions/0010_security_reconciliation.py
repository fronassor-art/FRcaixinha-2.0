"""reconcile security tables for databases that reached 0009 before v0.26

Revision ID: 0010_security_reconciliation
Revises: 0009_loan_financial_engine
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0010_security_reconciliation"
down_revision = "0009_loan_financial_engine"
branch_labels = None
depends_on = None

def _has_table(name):
    return inspect(op.get_bind()).has_table(name)

def upgrade():
    # Existing v0.26 databases may be stamped at 0009 and therefore skip
    # historical 0006. This reconciliation makes the upgrade safe/idempotent.
    if not _has_table("user_sessions"):
        op.create_table("user_sessions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("jti", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True)),
            sa.Column("ip_address", sa.String(64)),
            sa.Column("user_agent", sa.String(512)),
        )
        op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
        op.create_index("ix_user_sessions_jti", "user_sessions", ["jti"], unique=True)
        op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])
    if not _has_table("login_attempts"):
        op.create_table("login_attempts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("email", sa.String(255), nullable=False),
            sa.Column("ip_address", sa.String(64)),
            sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_login_attempts_email", "login_attempts", ["email"])
        op.create_index("ix_login_attempts_ip_address", "login_attempts", ["ip_address"])
        op.create_index("ix_login_attempts_created_at", "login_attempts", ["created_at"])
    if not _has_table("password_reset_tokens"):
        op.create_table("password_reset_tokens",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("token_hash", sa.String(128), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])
        op.create_index("ix_password_reset_tokens_token_hash", "password_reset_tokens", ["token_hash"], unique=True)
        op.create_index("ix_password_reset_tokens_expires_at", "password_reset_tokens", ["expires_at"])

def downgrade():
    # Deliberately conservative: reconciliation should not delete security data.
    pass
