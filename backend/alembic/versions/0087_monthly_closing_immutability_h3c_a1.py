"""make CLOSED monthly closings structurally immutable"""

from alembic import op
import sqlalchemy as sa


revision = "0087_monthly_closing_immutability_h3c_a1"
down_revision = "0086_payment_settlement_agreement_state_v106"
branch_labels = None
depends_on = None


SQLITE_UPDATE_TRIGGER = "trg_monthly_closing_closed_update"
SQLITE_DELETE_TRIGGER = "trg_monthly_closing_closed_delete"
POSTGRES_FUNCTION = "frcaixinha_prevent_monthly_closing_mutation"
POSTGRES_UPDATE_TRIGGER = "trg_monthly_closing_closed_update"
POSTGRES_DELETE_TRIGGER = "trg_monthly_closing_closed_delete"
MESSAGE = "MonthlyClosing CLOSED é imutável"


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute(sa.text(f"""
            CREATE TRIGGER {SQLITE_UPDATE_TRIGGER}
            BEFORE UPDATE ON monthly_closings
            FOR EACH ROW WHEN OLD.status = 'CLOSED'
            BEGIN
                SELECT RAISE(ABORT, '{MESSAGE}');
            END
        """))
        op.execute(sa.text(f"""
            CREATE TRIGGER {SQLITE_DELETE_TRIGGER}
            BEFORE DELETE ON monthly_closings
            FOR EACH ROW WHEN OLD.status = 'CLOSED'
            BEGIN
                SELECT RAISE(ABORT, '{MESSAGE}');
            END
        """))
    elif bind.dialect.name == "postgresql":
        op.execute(sa.text(f"""
            CREATE OR REPLACE FUNCTION {POSTGRES_FUNCTION}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF OLD.status = 'CLOSED' THEN
                    RAISE EXCEPTION '{MESSAGE}';
                END IF;
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END;
            $$
        """))
        op.execute(sa.text(f"""
            CREATE TRIGGER {POSTGRES_UPDATE_TRIGGER}
            BEFORE UPDATE ON monthly_closings
            FOR EACH ROW EXECUTE FUNCTION {POSTGRES_FUNCTION}()
        """))
        op.execute(sa.text(f"""
            CREATE TRIGGER {POSTGRES_DELETE_TRIGGER}
            BEFORE DELETE ON monthly_closings
            FOR EACH ROW EXECUTE FUNCTION {POSTGRES_FUNCTION}()
        """))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {SQLITE_UPDATE_TRIGGER}"))
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {SQLITE_DELETE_TRIGGER}"))
    elif bind.dialect.name == "postgresql":
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {POSTGRES_UPDATE_TRIGGER} ON monthly_closings"))
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {POSTGRES_DELETE_TRIGGER} ON monthly_closings"))
        op.execute(sa.text(f"DROP FUNCTION IF EXISTS {POSTGRES_FUNCTION}()"))
