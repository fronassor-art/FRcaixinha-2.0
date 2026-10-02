import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime, timezone
from sqlalchemy import text
from app.models import LedgerEntry, Payment, PaymentSettlement

CENT = Decimal("0.01")

def _lock_ledger_sequence(db):
    # PostgreSQL: serialize ledger inserts across workers/processes.
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('frcaixinha:ledger-sequence'))"))

def _hash_payload(entry, previous_hash):
    # V1 is the historical payload. Keep its fields and serialization stable.
    payload = {
        "account": entry.account, "direction": entry.direction,
        "amount": str(Decimal(entry.amount).quantize(CENT, rounding=ROUND_HALF_UP)),
        "reference_type": entry.reference_type, "reference_id": entry.reference_id,
        "reversal_of_id": entry.reversal_of_id,
        "created_at": entry.created_at.astimezone(timezone.utc).isoformat(),
        "previous_hash": previous_hash,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def _hash_payload_v2(entry, previous_hash):
    if entry.hash_version != 2 or not isinstance(entry.financial_date, date) or isinstance(entry.financial_date, datetime):
        raise ValueError("Ledger V2 exige hash_version=2 e financial_date civil")
    payload = {
        "account": entry.account, "direction": entry.direction,
        "amount": str(Decimal(entry.amount).quantize(CENT, rounding=ROUND_HALF_UP)),
        "reference_type": entry.reference_type, "reference_id": entry.reference_id,
        "reversal_of_id": entry.reversal_of_id,
        "created_at": entry.created_at.astimezone(timezone.utc).isoformat(),
        "previous_hash": previous_hash,
        "hash_version": 2,
        "financial_date": entry.financial_date.isoformat(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def _expected_entry_hash(entry, previous_hash):
    """Select the historical or V2 hash contract without changing either payload."""
    if entry.hash_version is None and entry.financial_date is None:
        return _hash_payload(entry, previous_hash), None
    if entry.hash_version == 2 and isinstance(entry.financial_date, date) and not isinstance(entry.financial_date, datetime):
        return _hash_payload_v2(entry, previous_hash), None
    if entry.hash_version not in (None, 2):
        return None, "unsupported_hash_version"
    return None, "invalid_hash_version_shape"

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

def post_entry_v2(
    db,
    account: str,
    direction: str,
    amount: Decimal,
    reference_type: str,
    reference_id: str,
    financial_date: date,
    reversal_of_id: int | None = None,
):
    """Create a V2 ledger entry with an explicit civil financial date.

    Timestamp-to-date conversion belongs to the caller. This API never
    derives a financial date from the technical created_at timestamp.
    """
    if not isinstance(financial_date, date) or isinstance(financial_date, datetime):
        raise ValueError("Ledger V2 exige financial_date do tipo date civil")

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
    entry = LedgerEntry(
        account=account,
        direction=direction,
        amount=amount,
        reference_type=reference_type,
        reference_id=reference_id,
        reversal_of_id=reversal_of_id,
        previous_hash=previous_hash,
        created_at=datetime.now(timezone.utc),
        financial_date=financial_date,
        hash_version=2,
    )
    entry.entry_hash = _hash_payload_v2(entry, previous_hash)
    db.add(entry)
    return entry

def post_contribution_payment(db, payment: Payment, *, amount: Decimal | None = None):
    if payment.ledger_posted_at is not None:
        return
    ref = str(payment.id)
    exists = db.query(LedgerEntry).filter(LedgerEntry.reference_type == "CONTRIBUTION_PAYMENT", LedgerEntry.reference_id == ref).first()
    if exists:
        payment.ledger_posted_at = datetime.now(timezone.utc)
        return
    post_entry(db, "CAIXINHA", "CREDIT", Decimal(payment.amount if amount is None else amount), "CONTRIBUTION_PAYMENT", ref)
    payment.ledger_posted_at = datetime.now(timezone.utc)

def reverse_entry(db, original: LedgerEntry, reason: str):
    if not reason or len(reason.strip()) < 5:
        raise ValueError("Informe um motivo de reversão com pelo menos 5 caracteres")
    payment_component_types = {
        "LOAN_INTEREST_PAYMENT",
        "LOAN_PENALTY_PAYMENT",
        "LOAN_INSTALLMENT_PAYMENT",
        "AGREEMENT_INSTALLMENT_PAYMENT",
    }
    if original.reference_type in payment_component_types and (original.reference_id or "").isdigit():
        settlement = db.query(PaymentSettlement).filter(
            PaymentSettlement.payment_id == int(original.reference_id),
        ).one_or_none()
        if settlement is not None:
            raise ValueError(
                "Componente de pagamento deve ser revertido pelo PaymentReversal integral."
            )
    return post_entry(db, original.account, "CREDIT" if original.direction == "DEBIT" else "DEBIT",
                      Decimal(original.amount), "REVERSAL", str(original.id), reversal_of_id=original.id)

def verify_ledger_chain(db):
    previous = None
    errors = []
    for entry in db.query(LedgerEntry).order_by(LedgerEntry.id.asc()).all():
        if entry.previous_hash != previous:
            errors.append({"id": entry.id, "reason": "previous_hash_mismatch"})
        expected, shape_error = _expected_entry_hash(entry, entry.previous_hash)
        if shape_error is not None:
            errors.append({"id": entry.id, "reason": shape_error})
        if expected is not None and entry.entry_hash != expected:
            errors.append({"id": entry.id, "reason": "entry_hash_mismatch"})
        previous = entry.entry_hash
    return {"status": "PASS" if not errors else "FAIL", "entries": db.query(LedgerEntry).count(), "errors": errors}


def ledger_entries_for_financial_period(db, *, start: datetime, end: datetime, financial_start: date, financial_end_exclusive: date, direction: str | None = None, reference_types: set[str] | None = None, exclude_ids: set[int] | None = None):
    """Select mixed V1/V2 rows without reinterpreting legacy timestamps."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("ledger technical bounds must be timezone-aware")
    if isinstance(financial_start, datetime) or isinstance(financial_end_exclusive, datetime):
        raise ValueError("ledger financial bounds must be civil dates")
    if financial_end_exclusive <= financial_start:
        raise ValueError("ledger financial bounds must be increasing")
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    if direction is not None and direction not in {"DEBIT", "CREDIT"}:
        raise ValueError("direction invalid")
    rows = db.query(LedgerEntry).order_by(LedgerEntry.id.asc()).all()
    if any(row.hash_version is not None or row.financial_date is not None for row in rows):
        if verify_ledger_chain(db)["status"] != "PASS":
            raise ValueError("invalid ledger chain")
    selected = []
    for row in rows:
        if (direction is not None and row.direction != direction) or (reference_types is not None and row.reference_type not in reference_types) or (exclude_ids and row.id in exclude_ids):
            continue
        if row.hash_version is None:
            if row.financial_date is not None:
                raise ValueError("Ledger V1 has financial_date")
            created_at = row.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            else:
                created_at = created_at.astimezone(timezone.utc)
            if start <= created_at < end:
                selected.append(row)
        elif row.hash_version == 2:
            if row.financial_date is None:
                raise ValueError("Ledger V2 has no financial_date")
            if financial_start <= row.financial_date < financial_end_exclusive:
                selected.append(row)
        else:
            raise ValueError("unknown Ledger hash version")
    return selected
