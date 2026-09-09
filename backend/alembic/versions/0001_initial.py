from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("cpf", sa.String(14), nullable=False),
        sa.Column("phone", sa.String(30)),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("accepted_terms_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email"), sa.UniqueConstraint("cpf")
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_cpf", "users", ["cpf"])

    op.create_table("groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("monthly_amount", sa.Numeric(14,2), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("due_day", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False)
    )

    op.create_table("members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False)
    )

    op.create_table("quotas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=False, unique=True),
        sa.Column("units", sa.Numeric(14,4), nullable=False),
        sa.Column("status", sa.String(20), nullable=False)
    )

    op.create_table("payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("provider_payment_id", sa.String(150), nullable=False),
        sa.Column("idempotency_key", sa.String(150), nullable=False, unique=True),
        sa.Column("amount", sa.Numeric(14,2), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("raw_status", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "provider_payment_id")
    )
    op.create_index("ix_payments_idempotency_key", "payments", ["idempotency_key"])

    op.create_table("contributions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("competence", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(14,2), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("member_id", "competence")
    )

    op.create_table("loans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("principal", sa.Numeric(14,2), nullable=False),
        sa.Column("monthly_rate", sa.Numeric(8,5), nullable=False),
        sa.Column("installments", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by", sa.Integer(), sa.ForeignKey("users.id"))
    )

    op.create_table("loan_installments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("loan_id", sa.Integer(), sa.ForeignKey("loans.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("principal", sa.Numeric(14,2), nullable=False),
        sa.Column("interest", sa.Numeric(14,2), nullable=False),
        sa.Column("amount", sa.Numeric(14,2), nullable=False),
        sa.Column("paid_amount", sa.Numeric(14,2), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.UniqueConstraint("loan_id", "number")
    )

    op.create_table("ledger_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account", sa.String(80), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("amount", sa.Numeric(14,2), nullable=False),
        sa.Column("reference_type", sa.String(50), nullable=False),
        sa.Column("reference_id", sa.String(80), nullable=False),
        sa.Column("reversal_of_id", sa.Integer(), sa.ForeignKey("ledger_entries.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)
    )
    op.create_index("ix_ledger_entries_account", "ledger_entries", ["account"])
    op.create_index("ix_ledger_reference", "ledger_entries", ["reference_type", "reference_id"])

    op.create_table("audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.String(80), nullable=False),
        sa.Column("details", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)
    )

def downgrade():
    op.drop_table("audit_logs")
    op.drop_index("ix_ledger_reference", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_account", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_table("loan_installments")
    op.drop_table("loans")
    op.drop_table("contributions")
    op.drop_index("ix_payments_idempotency_key", table_name="payments")
    op.drop_table("payments")
    op.drop_table("quotas")
    op.drop_table("members")
    op.drop_table("groups")
    op.drop_index("ix_users_cpf", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
