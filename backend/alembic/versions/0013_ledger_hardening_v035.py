"""v0.35: append-only ledger, hash chain and controlled reversals."""
from alembic import op
import sqlalchemy as sa
import hashlib, json
from decimal import Decimal
from datetime import timezone

revision = "0013_ledger_hardening_v035"
down_revision = "0012_monthly_closing_integrity_v034"
branch_labels = None
depends_on = None

def _hash(row, previous_hash):
    amount = Decimal(row[3]).quantize(Decimal("0.01"))
    payload = {"account":row[1],"direction":row[2],"amount":str(amount),"reference_type":row[4],"reference_id":row[5],"reversal_of_id":row[6],"created_at":row[7].isoformat() if hasattr(row[7],"isoformat") else str(row[7]),"previous_hash":previous_hash}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def upgrade():
    op.add_column("ledger_entries", sa.Column("previous_hash", sa.String(64), nullable=True))
    op.add_column("ledger_entries", sa.Column("entry_hash", sa.String(64), nullable=True))
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, account, direction, amount, reference_type, reference_id, reversal_of_id, created_at FROM ledger_entries ORDER BY id")).fetchall()
    previous = None
    for r in rows:
        h = _hash(r, previous)
        bind.execute(sa.text("UPDATE ledger_entries SET previous_hash=:p, entry_hash=:h WHERE id=:id"), {"p":previous,"h":h,"id":r[0]})
        previous = h
    op.create_index("ix_ledger_entries_previous_hash", "ledger_entries", ["previous_hash"])
    op.create_unique_constraint("uq_ledger_entries_entry_hash", "ledger_entries", ["entry_hash"])
    # At most one reversal per original entry.
    op.create_index("uq_ledger_entries_one_reversal", "ledger_entries", ["reversal_of_id"], unique=True, postgresql_where=sa.text("reversal_of_id IS NOT NULL"), sqlite_where=sa.text("reversal_of_id IS NOT NULL"))
    if bind.dialect.name == "postgresql":
        op.execute(sa.text("""CREATE OR REPLACE FUNCTION frcaixinha_prevent_ledger_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'LedgerEntry é imutável; use reversão controlada'; END; $$"""))
        op.execute(sa.text("""CREATE TRIGGER trg_ledger_append_only BEFORE UPDATE OR DELETE ON ledger_entries FOR EACH ROW EXECUTE FUNCTION frcaixinha_prevent_ledger_mutation()"""))

def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_ledger_append_only ON ledger_entries"))
        op.execute(sa.text("DROP FUNCTION IF EXISTS frcaixinha_prevent_ledger_mutation()"))
    op.drop_index("uq_ledger_entries_one_reversal", table_name="ledger_entries")
    op.drop_constraint("uq_ledger_entries_entry_hash", "ledger_entries", type_="unique")
    op.drop_index("ix_ledger_entries_previous_hash", table_name="ledger_entries")
    op.drop_column("ledger_entries", "entry_hash")
    op.drop_column("ledger_entries", "previous_hash")
