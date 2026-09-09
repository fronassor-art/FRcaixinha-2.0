"""admin indexes

Revision ID: 0004_admin_indexes
Revises: 0003_payment_ledger
"""
from alembic import op

revision = "0004_admin_indexes"
down_revision = "0003_payment_ledger"
branch_labels = None
depends_on = None

def upgrade():
    op.create_index("ix_members_status", "members", ["status"])
    op.create_index("ix_contributions_status", "contributions", ["status"])
    op.create_index("ix_contributions_competence", "contributions", ["competence"])
    op.create_index("ix_loans_status", "loans", ["status"])
    op.create_index("ix_installments_status_due", "loan_installments", ["status", "due_date"])
    op.create_index("ix_audit_created_at", "audit_logs", ["created_at"])

def downgrade():
    op.drop_index("ix_audit_created_at", table_name="audit_logs")
    op.drop_index("ix_installments_status_due", table_name="loan_installments")
    op.drop_index("ix_loans_status", table_name="loans")
    op.drop_index("ix_contributions_competence", table_name="contributions")
    op.drop_index("ix_contributions_status", table_name="contributions")
    op.drop_index("ix_members_status", table_name="members")
