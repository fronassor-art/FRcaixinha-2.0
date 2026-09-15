"""add persisted Master Administrator flag"""

from alembic import op
import sqlalchemy as sa


revision = "0080_master_administrator_v104"
down_revision = "0079_pix_payment_settlement_v103"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column(
            "is_master",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # The predicate scopes the unique index to active Master rows. The
    # indexed value is constant for rows in the predicate, so only one such
    # row can exist. Both predicates are explicit because SQLite and
    # PostgreSQL render boolean literals differently.
    op.create_index(
        "uq_users_one_active_master",
        "users",
        ["is_master"],
        unique=True,
        postgresql_where=sa.text("is_master = true AND is_active = true AND role = 'ADMIN'"),
        sqlite_where=sa.text("is_master = 1 AND is_active = 1 AND role = 'ADMIN'"),
    )


def downgrade():
    op.drop_index(
        "uq_users_one_active_master",
        table_name="users",
        postgresql_where=sa.text("is_master = true AND is_active = true AND role = 'ADMIN'"),
        sqlite_where=sa.text("is_master = 1 AND is_active = 1 AND role = 'ADMIN'"),
    )
    op.drop_column("users", "is_master")
