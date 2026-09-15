"""enforce Master Administrator integrity"""

from alembic import op
import sqlalchemy as sa


revision = "0081_master_integrity_v104"
down_revision = "0080_master_administrator_v104"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_users_master_requires_active_admin"
_PREDICATE = "is_master = true AND NOT (role = 'ADMIN' AND is_active = true)"


def upgrade():
    bind = op.get_bind()
    invalid_ids = [row[0] for row in bind.execute(sa.text(f"SELECT id FROM users WHERE {_PREDICATE} ORDER BY id"))]
    if invalid_ids:
        raise RuntimeError(
            "users contém is_master inválido; migração abortada sem correção automática: "
            + ",".join(str(user_id) for user_id in invalid_ids)
        )

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("users", recreate="always") as batch_op:
            batch_op.create_check_constraint(
                _CONSTRAINT,
                "is_master = false OR (role = 'ADMIN' AND is_active = true)",
            )
    else:
        op.create_check_constraint(
            _CONSTRAINT,
            "users",
            "is_master = false OR (role = 'ADMIN' AND is_active = true)",
        )


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("users", recreate="always") as batch_op:
            batch_op.drop_constraint(_CONSTRAINT, type_="check")
    else:
        op.drop_constraint(_CONSTRAINT, "users", type_="check")
