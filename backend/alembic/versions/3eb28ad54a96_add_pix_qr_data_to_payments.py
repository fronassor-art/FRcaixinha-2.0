"""add pix qr data to payments

Revision ID: 3eb28ad54a96
Revises: 7df0a77ffd28
Create Date: 2026-09-09
"""

from alembic import op
import sqlalchemy as sa


revision = "3eb28ad54a96"
down_revision = "7df0a77ffd28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column("qr_code", sa.Text(), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("qr_code_base64", sa.Text(), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("ticket_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payments", "ticket_url")
    op.drop_column("payments", "qr_code_base64")
    op.drop_column("payments", "qr_code")
