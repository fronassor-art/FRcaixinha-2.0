import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
from sqlalchemy import text
from app.models import LedgerEntry, Payment

CENT = Decimal("0.01")

def _lock_ledger_sequence(db):
    # PostgreSQL: serialize ledger inserts across workers/processes.
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('frcaixinha:ledger-sequence'))"))

def _hash_payload(entry, previous_hash):
    payload = {
        "account": entry.account, "direction": entry.direction,
        "amount": str(Decimal(entry.amount).quantize(CENT, rounding=ROUND_HALF_UP)),
        "reference_type": entry.reference_type, "reference_id": entry.reference_id,
        "reversal_of_id": entry.reversal_of_id,
        "created_at": entry.created_at.isoformat(),
        "previous_hash": previous_hash,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def post_entry(db, account: str, direction: str, amount: Decimal, reference_type: str, reference_id: str, reversal_of_id: int | None = None):
    amount = Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)
    if amount <= 0:
        raise ValueError("amount deve ser positivo")
    if direction not in {"DEBIT", "CREDIT"}:
        raise ValueError("direction inválida")
    if reversal_of_id is not None:
        original = db.get(LedgerEntry, reversal_of_id)
        if not original:
            raise ValueError("Lançamento original não encontrado")
        if original.reversal_of_id is not None:
            raise ValueError("Não é permitido reverter uma reversão")
        existing = db.query(LedgerEntry).filter(LedgerEntry.reversal_of_id == reversal_of_id).first()
        if existing:
            raise ValueError("Lançamento já possui reversão")
        if amount != Decimal(original.amount):
            raise ValueError("A reversão deve ter o mesmo valor do lançamento original")
    _lock_ledger_sequence(db)
    previous = db.query(LedgerEntry).order_by(LedgerEntry.id.desc()).first()
    previous_hash = previous.entry_hash if previous else None
    created_at = datetime.now(timezone.utc)
    entry = LedgerEntry(account=account, direction=direction, amount=amount,
                        reference_type=reference_type, reference_id=reference_id,
                        reversal_of_id=reversal_of_id, previous_hash=previous_hash, created_at=created_at)
    entry.entry_hash = _hash_payload(entry, previous_hash)
    db.add(entry)
    return entry

def post_contribution_payment(db, payment: Payment):
    if payment.ledger_posted_at is not None:
        return
    ref = str(payment.id)
    exists = db.query(LedgerEntry).filter(LedgerEntry.reference_type == "CONTRIBUTION_PAYMENT", LedgerEntry.reference_id == ref).first()
    if exists:
        payment.ledger_posted_at = datetime.now(timezone.utc)
        return
    post_entry(db, "CAIXINHA", "CREDIT", Decimal(payment.amount), "CONTRIBUTION_PAYMENT", ref)
    payment.ledger_posted_at = datetime.now(timezone.utc)

def reverse_entry(db, original: LedgerEntry, reason: str):
    if not reason or len(reason.strip()) < 5:
        raise ValueError("Informe um motivo de reversão com pelo menos 5 caracteres")
    return post_entry(db, original.account, "CREDIT" if original.direction == "DEBIT" else "DEBIT",
                      Decimal(original.amount), "REVERSAL", str(original.id), reversal_of_id=original.id)

def verify_ledger_chain(db):
    previous = None
    errors = []
    for entry in db.query(LedgerEntry).order_by(LedgerEntry.id.asc()).all():
        if entry.previous_hash != previous:
            errors.append({"id": entry.id, "reason": "previous_hash_mismatch"})
        expected = _hash_payload(entry, entry.previous_hash)
        if entry.entry_hash != expected:
            errors.append({"id": entry.id, "reason": "entry_hash_mismatch"})
        previous = entry.entry_hash
    return {"status": "PASS" if not errors else "FAIL", "entries": db.query(LedgerEntry).count(), "errors": errors}
