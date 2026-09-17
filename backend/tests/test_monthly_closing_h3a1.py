import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models import MonthlyClosing
from app.services.monthly_closing_v040 import close_month_v040


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


def test_monthly_closing_maps_snapshot_columns_to_existing_schema():
    mapper = inspect(MonthlyClosing)
    columns = {column.key: column for column in mapper.columns}

    assert "snapshot_json" in columns
    assert "snapshot_hash" in columns
    assert columns["snapshot_json"].nullable is True
    assert columns["snapshot_hash"].nullable is True
    assert columns["snapshot_hash"].type.length == 64
    assert columns["snapshot_hash"].unique is True


def test_monthly_closing_snapshot_survives_commit_reload_and_new_session():
    engine, sessions = _session_factory()
    canonical = json.dumps({"competence": "2026-12-01", "value": "10.00"}, separators=(",", ":"), sort_keys=True)
    snapshot_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    db = sessions()
    row = MonthlyClosing(
        competence=date(2026, 12, 1),
        status="CLOSED",
        snapshot_json=canonical,
        snapshot_hash=snapshot_hash,
    )
    db.add(row)
    db.commit()
    db.expire(row)

    assert row.snapshot_json == canonical
    assert row.snapshot_hash == snapshot_hash
    db.close()

    fresh = sessions()
    loaded = fresh.query(MonthlyClosing).filter_by(competence=date(2026, 12, 1)).one()
    assert loaded.snapshot_json == canonical
    assert loaded.snapshot_hash == snapshot_hash
    fresh.close()
    engine.dispose()


def test_real_close_persists_snapshot_and_hash_after_reload():
    engine, sessions = _session_factory()
    db = sessions()

    closing, snapshot, snapshot_hash = close_month_v040(
        db,
        date(2026, 9, 1),
        admin_id=1,
    )
    db.commit()
    db.expire(closing)

    assert closing.status == "CLOSED"
    assert closing.snapshot_json is not None
    assert closing.snapshot_hash is not None
    assert len(closing.snapshot_hash) == 64
    assert json.loads(closing.snapshot_json) == snapshot
    assert closing.snapshot_hash == snapshot_hash
    db.close()

    fresh = sessions()
    loaded = fresh.query(MonthlyClosing).filter_by(competence=date(2026, 9, 1)).one()
    assert loaded.status == "CLOSED"
    assert json.loads(loaded.snapshot_json) == snapshot
    assert loaded.snapshot_hash == snapshot_hash
    fresh.close()
    engine.dispose()


def test_existing_closed_closing_remains_unchanged_after_reload():
    engine, sessions = _session_factory()
    canonical = json.dumps({"schema": "v0.40", "competence": "2026-01-01"}, separators=(",", ":"), sort_keys=True)
    snapshot_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    closed_at = datetime(2026, 2, 1, tzinfo=timezone.utc)

    db = sessions()
    row = MonthlyClosing(
        competence=date(2026, 1, 1),
        status="CLOSED",
        total_contributions="100.00",
        total_expenses="5.00",
        total_interest_received="20.00",
        ledger_balance="115.00",
        closed_by=7,
        closed_at=closed_at,
        snapshot_json=canonical,
        snapshot_hash=snapshot_hash,
    )
    db.add(row)
    db.commit()
    db.close()

    fresh = sessions()
    loaded = fresh.query(MonthlyClosing).filter_by(competence=date(2026, 1, 1)).one()
    assert loaded.status == "CLOSED"
    assert loaded.total_contributions == Decimal("100.00")
    assert loaded.total_expenses == Decimal("5.00")
    assert loaded.total_interest_received == Decimal("20.00")
    assert loaded.ledger_balance == Decimal("115.00")
    assert loaded.closed_by == 7
    assert loaded.snapshot_json == canonical
    assert loaded.snapshot_hash == snapshot_hash
    fresh.close()
    engine.dispose()
