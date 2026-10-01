"""Schema and mixed hash-chain foundation for ledger financial dates."""

import hashlib
import importlib.util
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import LedgerEntry
from app.services.ledger import (
    _hash_payload,
    _hash_payload_v2,
    post_entry,
    post_entry_v2,
    verify_ledger_chain,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = BACKEND_ROOT / "alembic" / "versions" / "0105_ledger_financial_date_f1.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("ledger_financial_date_f1", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _db():
    engine = _engine()
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def _v1_digest(*, account, direction, amount, reference_type, reference_id, reversal_of_id, created_at, previous_hash):
    payload = {
        "account": account,
        "direction": direction,
        "amount": str(Decimal(amount).quantize(Decimal("0.01"))),
        "reference_type": reference_type,
        "reference_id": reference_id,
        "reversal_of_id": reversal_of_id,
        "created_at": created_at.isoformat(),
        "previous_hash": previous_hash,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _apply_migration(connection, method):
    migration = _load_migration()
    with Operations.context(MigrationContext.configure(connection)):
        getattr(migration, method)()


def test_upgrade_preserves_legacy_rows_hashes_and_does_not_backfill():
    engine = _engine()
    metadata = sa.MetaData()
    ledger = sa.Table(
        "ledger_entries",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account", sa.String(80), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("reference_type", sa.String(50), nullable=False),
        sa.Column("reference_id", sa.String(80), nullable=False),
        sa.Column("reversal_of_id", sa.Integer(), sa.ForeignKey("ledger_entries.id")),
        sa.Column("previous_hash", sa.String(64)),
        sa.Column("entry_hash", sa.String(64), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Index("ix_ledger_entries_account", ledger.c.account)
    sa.Index("ix_ledger_reference", ledger.c.reference_type, ledger.c.reference_id)
    sa.Index("ix_ledger_entries_previous_hash", ledger.c.previous_hash)
    metadata.create_all(engine)

    created1 = datetime(2026, 9, 30, 23, 55, tzinfo=timezone.utc)
    created2 = datetime(2026, 10, 1, 0, 3, tzinfo=timezone.utc)
    h1 = _v1_digest(
        account="CAIXINHA", direction="CREDIT", amount="10.00",
        reference_type="TEST", reference_id="legacy-1", reversal_of_id=None,
        created_at=created1, previous_hash=None,
    )
    h2 = _v1_digest(
        account="CAIXINHA", direction="DEBIT", amount="2.00",
        reference_type="TEST", reference_id="legacy-2", reversal_of_id=None,
        created_at=created2, previous_hash=h1,
    )
    with engine.begin() as connection:
        connection.execute(ledger.insert(), [
            {"id": 1, "account": "CAIXINHA", "direction": "CREDIT", "amount": Decimal("10.00"), "reference_type": "TEST", "reference_id": "legacy-1", "reversal_of_id": None, "previous_hash": None, "entry_hash": h1, "created_at": created1},
            {"id": 2, "account": "CAIXINHA", "direction": "DEBIT", "amount": Decimal("2.00"), "reference_type": "TEST", "reference_id": "legacy-2", "reversal_of_id": None, "previous_hash": h1, "entry_hash": h2, "created_at": created2},
        ])
        _apply_migration(connection, "upgrade")
        rows = connection.execute(
            text("SELECT id, previous_hash, entry_hash, financial_date, hash_version FROM ledger_entries ORDER BY id")
        ).all()
        assert [(row.id, row.previous_hash, row.entry_hash, row.financial_date, row.hash_version) for row in rows] == [
            (1, None, h1, None, None), (2, h1, h2, None, None),
        ]
        indexes = {item["name"] for item in sa.inspect(connection).get_indexes("ledger_entries")}
        assert {
            "ix_ledger_entries_financial_date", "ix_ledger_entries_account",
            "ix_ledger_reference", "ix_ledger_entries_previous_hash",
        } <= indexes
        unique_constraints = sa.inspect(connection).get_unique_constraints("ledger_entries")
        assert any(constraint["column_names"] == ["entry_hash"] for constraint in unique_constraints)

    engine.dispose()


def test_migration_downgrade_refuses_to_discard_v2_data():
    engine = _engine()
    metadata = sa.MetaData()
    ledger = sa.Table(
        "ledger_entries", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account", sa.String(80), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("reference_type", sa.String(50), nullable=False),
        sa.Column("reference_id", sa.String(80), nullable=False),
        sa.Column("reversal_of_id", sa.Integer(), sa.ForeignKey("ledger_entries.id")),
        sa.Column("previous_hash", sa.String(64)),
        sa.Column("entry_hash", sa.String(64), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        _apply_migration(connection, "upgrade")
        connection.execute(text(
            "INSERT INTO ledger_entries (account,direction,amount,reference_type,reference_id,created_at,financial_date,hash_version) "
            "VALUES ('CAIXINHA','CREDIT',1,'TEST','v2','2026-01-01T00:00:00+00:00','2026-01-01',2)"
        ))
        with pytest.raises(RuntimeError, match="versioned ledger rows"):
            _apply_migration(connection, "downgrade")
        assert "financial_date" in {column["name"] for column in sa.inspect(connection).get_columns("ledger_entries")}
    engine.dispose()


def test_migration_downgrade_preserves_legacy_hashes():
    engine = _engine()
    metadata = sa.MetaData()
    ledger = sa.Table(
        "ledger_entries", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account", sa.String(80), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("reference_type", sa.String(50), nullable=False),
        sa.Column("reference_id", sa.String(80), nullable=False),
        sa.Column("reversal_of_id", sa.Integer(), sa.ForeignKey("ledger_entries.id")),
        sa.Column("previous_hash", sa.String(64)),
        sa.Column("entry_hash", sa.String(64), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(engine)
    digest = "a" * 64
    with engine.begin() as connection:
        connection.execute(ledger.insert(), {
            "id": 1, "account": "CAIXINHA", "direction": "CREDIT", "amount": Decimal("1.00"),
            "reference_type": "TEST", "reference_id": "legacy", "reversal_of_id": None,
            "previous_hash": None, "entry_hash": digest,
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        })
        _apply_migration(connection, "upgrade")
        _apply_migration(connection, "downgrade")
        row = connection.execute(text("SELECT previous_hash, entry_hash FROM ledger_entries WHERE id=1")).one()
        assert tuple(row) == (None, digest)
        assert {column["name"] for column in sa.inspect(connection).get_columns("ledger_entries")} == {
            "id", "account", "direction", "amount", "reference_type", "reference_id",
            "reversal_of_id", "previous_hash", "entry_hash", "created_at",
        }
    engine.dispose()


def _append_v2(db, *, reference_id, financial_day, created_at, previous_hash):
    entry = LedgerEntry(
        account="CAIXINHA", direction="CREDIT", amount=Decimal("3.25"),
        reference_type="F1_TEST", reference_id=reference_id, reversal_of_id=None,
        previous_hash=previous_hash, created_at=created_at,
        financial_date=financial_day, hash_version=2,
    )
    entry.entry_hash = _hash_payload_v2(entry, previous_hash)
    db.add(entry)
    db.flush()
    return entry


def test_v1_chain_and_v1_to_v2_to_v2_chain_verify_without_changing_v1_hash():
    engine, db = _db()
    legacy = post_entry(db, "CAIXINHA", "DEBIT", Decimal("1.00"), "F1_TEST", "v1")
    db.flush()
    legacy_hash = legacy.entry_hash
    first_v2 = _append_v2(
        db, reference_id="v2-1", financial_day=date(2026, 9, 30),
        created_at=datetime(2026, 10, 1, 3, 3, tzinfo=timezone.utc), previous_hash=legacy_hash,
    )
    second_v2 = _append_v2(
        db, reference_id="v2-2", financial_day=date(2026, 10, 1),
        created_at=datetime(2026, 10, 1, 4, 3, tzinfo=timezone.utc), previous_hash=first_v2.entry_hash,
    )
    hashes = [legacy.entry_hash, first_v2.entry_hash, second_v2.entry_hash]
    assert legacy.hash_version is None and legacy.financial_date is None
    assert verify_ledger_chain(db)["status"] == "PASS"
    assert [row.entry_hash for row in db.query(LedgerEntry).order_by(LedgerEntry.id)] == hashes
    db.close()
    engine.dispose()


def test_post_entry_keeps_v1_null_metadata_payload_and_v1_chain():
    engine, db = _db()
    first = post_entry(db, "CAIXINHA", "CREDIT", Decimal("12.30"), "F2B_TEST", "v1-1")
    db.flush()
    second = post_entry(db, "CAIXINHA", "DEBIT", Decimal("2.30"), "F2B_TEST", "v1-2")
    db.flush()

    assert first.financial_date is None and first.hash_version is None
    assert second.financial_date is None and second.hash_version is None
    assert first.entry_hash == _hash_payload(first, None)
    assert second.previous_hash == first.entry_hash
    assert second.entry_hash == _hash_payload(second, first.entry_hash)
    assert verify_ledger_chain(db) == {"status": "PASS", "entries": 2, "errors": []}

    db.close()
    engine.dispose()


def test_post_entry_v2_persists_explicit_date_and_verifies():
    engine, db = _db()
    requested_date = date(2026, 9, 30)
    entry = post_entry_v2(
        db, "CAIXINHA", "CREDIT", Decimal("12.30"), "F2B_TEST", "v2-one",
        financial_date=requested_date,
    )
    db.flush()

    assert entry.financial_date == requested_date
    assert entry.hash_version == 2
    assert entry.created_at.tzinfo is not None
    assert entry.entry_hash == _hash_payload_v2(entry, None)
    assert verify_ledger_chain(db) == {"status": "PASS", "entries": 1, "errors": []}

    db.close()
    engine.dispose()


def test_post_entry_v2_requires_date_and_does_not_accept_a_version_override():
    engine, db = _db()
    legacy = post_entry(db, "CAIXINHA", "CREDIT", Decimal("5.00"), "F2B_TEST", "stable-v1")
    db.flush()
    before = (db.query(LedgerEntry).count(), legacy.entry_hash, verify_ledger_chain(db))
    args = (db, "CAIXINHA", "DEBIT", Decimal("1.00"), "F2B_TEST", "invalid")

    with pytest.raises(TypeError):
        post_entry_v2(*args)
    with pytest.raises(TypeError):
        post_entry_v2(*args, financial_date=date(2026, 9, 30), hash_version=1)

    assert (db.query(LedgerEntry).count(), legacy.entry_hash, verify_ledger_chain(db)) == before
    db.close()
    engine.dispose()


@pytest.mark.parametrize(
    "invalid_date",
    [None, datetime(2026, 9, 30, tzinfo=timezone.utc), "2026-09-30"],
)
def test_post_entry_v2_rejects_non_date_without_changing_chain(invalid_date):
    engine, db = _db()
    legacy = post_entry(db, "CAIXINHA", "CREDIT", Decimal("5.00"), "F2B_TEST", "before-invalid")
    db.flush()
    before = (db.query(LedgerEntry).count(), legacy.entry_hash, verify_ledger_chain(db))

    with pytest.raises(ValueError, match="date civil"):
        post_entry_v2(
            db, "CAIXINHA", "DEBIT", Decimal("1.00"), "F2B_TEST", "invalid-date",
            financial_date=invalid_date,
        )

    assert (db.query(LedgerEntry).count(), legacy.entry_hash, verify_ledger_chain(db)) == before
    db.close()
    engine.dispose()


def test_post_entry_v2_builds_v1_v2_v2_chain_and_authenticates_previous_hash():
    engine, db = _db()
    first_v1 = post_entry(db, "CAIXINHA", "CREDIT", Decimal("8.00"), "F2B_TEST", "chain-v1")
    db.flush()
    first_v2 = post_entry_v2(
        db, "CAIXINHA", "DEBIT", Decimal("1.00"), "F2B_TEST", "chain-v2-1",
        financial_date=date(2026, 9, 30),
    )
    db.flush()
    second_v2 = post_entry_v2(
        db, "CAIXINHA", "CREDIT", Decimal("2.00"), "F2B_TEST", "chain-v2-2",
        financial_date=date(2026, 10, 1),
    )
    db.flush()
    assert first_v2.previous_hash == first_v1.entry_hash
    assert second_v2.previous_hash == first_v2.entry_hash
    assert verify_ledger_chain(db)["status"] == "PASS"

    db.execute(
        text("UPDATE ledger_entries SET previous_hash=:bad WHERE id=:id"),
        {"bad": "b" * 64, "id": second_v2.id},
    )
    db.expire_all()
    assert {error["reason"] for error in verify_ledger_chain(db)["errors"]} >= {
        "previous_hash_mismatch", "entry_hash_mismatch",
    }

    db.close()
    engine.dispose()


def test_v2_financial_date_and_created_at_are_authenticated():
    engine, db = _db()
    entry = _append_v2(
        db, reference_id="tamper-date", financial_day=date(2026, 9, 30),
        created_at=datetime(2026, 10, 1, 3, 3, tzinfo=timezone.utc), previous_hash=None,
    )
    assert verify_ledger_chain(db)["status"] == "PASS"
    db.execute(text("UPDATE ledger_entries SET financial_date='2026-10-01' WHERE id=:id"), {"id": entry.id})
    db.expire_all()
    assert {error["reason"] for error in verify_ledger_chain(db)["errors"]} == {"entry_hash_mismatch"}
    db.execute(text("UPDATE ledger_entries SET financial_date='2026-09-30' WHERE id=:id"), {"id": entry.id})
    # Bypass the schema guard only to exercise verifier behavior for an
    # impossible persisted version; the production constraint remains intact.
    db.execute(text("PRAGMA ignore_check_constraints=ON"))
    db.execute(text("UPDATE ledger_entries SET hash_version=3 WHERE id=:id"), {"id": entry.id})
    db.expire_all()
    assert "unsupported_hash_version" in {error["reason"] for error in verify_ledger_chain(db)["errors"]}
    db.execute(text("UPDATE ledger_entries SET hash_version=2 WHERE id=:id"), {"id": entry.id})
    db.execute(text("UPDATE ledger_entries SET created_at='2026-10-01 03:04:00+00:00' WHERE id=:id"), {"id": entry.id})
    db.expire_all()
    assert {error["reason"] for error in verify_ledger_chain(db)["errors"]} == {"entry_hash_mismatch"}
    db.close()
    engine.dispose()


@pytest.mark.parametrize(
    ("financial_day", "version"),
    [(date(2026, 1, 1), None), (None, 2), (date(2026, 1, 1), 1), (None, 3)],
)
def test_constraint_rejects_invalid_financial_date_hash_version_pairs(financial_day, version):
    engine, db = _db()
    db.add(LedgerEntry(
        account="CAIXINHA", direction="CREDIT", amount=Decimal("1.00"),
        reference_type="F1_TEST", reference_id="invalid", created_at=datetime.now(timezone.utc),
        financial_date=financial_day, hash_version=version,
    ))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
    db.close()
    engine.dispose()


@pytest.mark.parametrize(
    ("financial_day", "version", "expected_reason"),
    [(date(2026, 1, 1), 3, "unsupported_hash_version"), (None, 2, "invalid_hash_version_shape")],
)
def test_verifier_fails_closed_for_unknown_version_or_v2_without_date(financial_day, version, expected_reason):
    engine, db = _db()
    db.execute(text("PRAGMA ignore_check_constraints=ON"))
    entry = LedgerEntry(
        account="CAIXINHA", direction="CREDIT", amount=Decimal("1.00"),
        reference_type="F1_TEST", reference_id="invalid-verifier", created_at=datetime.now(timezone.utc),
        financial_date=financial_day, hash_version=version, previous_hash=None, entry_hash="f" * 64,
    )
    db.add(entry)
    db.flush()
    result = verify_ledger_chain(db)
    assert result["status"] == "FAIL"
    assert expected_reason in {error["reason"] for error in result["errors"]}
    db.close()
    engine.dispose()


def test_v1_helper_keeps_the_historical_payload_contract():
    engine, db = _db()
    entry = LedgerEntry(
        account="CAIXINHA", direction="CREDIT", amount=Decimal("12.30"),
        reference_type="F1_TEST", reference_id="legacy-payload", reversal_of_id=None,
        previous_hash=None, created_at=datetime(2026, 9, 30, 23, 55, tzinfo=timezone.utc),
    )
    expected = _v1_digest(
        account=entry.account, direction=entry.direction, amount=entry.amount,
        reference_type=entry.reference_type, reference_id=entry.reference_id,
        reversal_of_id=entry.reversal_of_id, created_at=entry.created_at, previous_hash=None,
    )
    assert _hash_payload(entry, None) == expected
    db.close()
    engine.dispose()
