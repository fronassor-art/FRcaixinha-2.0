import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import subprocess
import sys

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import Payment


REVISION = "0093_payment_attempt_schema_foundation"
PREVIOUS_REVISION = "0092_versioned_late_charge_foundation"
HEAD_REVISION = "0098_cycle_closing_persistence_a377b2"
INDEX_NAME = "uq_payments_reference_pending"


def _backend_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _alembic(database: Path, *args: str) -> None:
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{database}",
            "JWT_SECRET": "testsecret",
            "APP_ENV": "test",
        }
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_backend_dir(),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _insert_legacy_payment(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO payments (
                    provider,
                    provider_payment_id,
                    idempotency_key,
                    amount,
                    status,
                    confirmed_at,
                    amount_received,
                    provider_payload_json,
                    reference_type,
                    reference_id,
                    expires_at,
                    created_at
                )
                VALUES (
                    'legacy_provider',
                    'legacy-payment-1',
                    'legacy-idempotency-1',
                    50.00,
                    'CONFIRMED',
                    '2026-09-20 10:00:00',
                    50.00,
                    :provider_payload_json,
                    'CONTRIBUTION',
                    'legacy-ref-1',
                    '2026-09-30 12:00:00',
                    '2026-09-20 12:00:00'
                )
                """
            ),
            {"provider_payload_json": '{"legacy":true}'},
        )


def test_revision_chain_and_single_head():
    config = Config(str(_backend_dir() / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(_backend_dir() / "alembic"),
    )
    script = ScriptDirectory.from_config(config)

    assert script.get_current_head() == HEAD_REVISION
    assert script.get_revision(REVISION).down_revision == PREVIOUS_REVISION
    assert tuple(script.get_heads()) == (HEAD_REVISION,)


def test_payment_orm_contract_and_storage_types():
    columns = Payment.__table__.c

    assert columns.attempt_status.type.python_type is str
    assert columns.calculated_for_date.type.python_type is date
    assert columns.financial_snapshot_json.type.python_type is str
    assert columns.snapshot_hash.type.python_type is str
    assert columns.reconciliation_status.type.python_type is str

    assert columns.attempt_status.nullable
    assert columns.calculated_for_date.nullable
    assert columns.financial_snapshot_json.nullable
    assert columns.snapshot_hash.nullable
    assert columns.reconciliation_status.nullable

    assert columns.snapshot_hash.type.length == 64

    indexes = {index.name: index for index in Payment.__table__.indexes}
    assert INDEX_NAME in indexes
    assert indexes[INDEX_NAME].unique


def test_nullable_legacy_compatibility_and_no_financial_mutation(tmp_path):
    database = tmp_path / "a375b_legacy.db"

    _alembic(database, "upgrade", PREVIOUS_REVISION)

    engine = create_engine(f"sqlite:///{database}")
    _insert_legacy_payment(engine)

    with engine.connect() as connection:
        before = connection.execute(
            text(
                """
                SELECT
                    amount,
                    status,
                    confirmed_at,
                    amount_received,
                    provider_payment_id,
                    idempotency_key,
                    reference_type,
                    reference_id,
                    expires_at,
                    provider_payload_json
                FROM payments
                WHERE provider_payment_id = 'legacy-payment-1'
                """
            )
        ).mappings().one()

    engine.dispose()

    _alembic(database, "upgrade", REVISION)

    engine = create_engine(f"sqlite:///{database}")

    columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("payments")
    }

    for name in (
        "attempt_status",
        "calculated_for_date",
        "financial_snapshot_json",
        "snapshot_hash",
        "reconciliation_status",
    ):
        assert columns[name]["nullable"]

    with engine.connect() as connection:
        after = connection.execute(
            text(
                """
                SELECT
                    amount,
                    status,
                    confirmed_at,
                    amount_received,
                    provider_payment_id,
                    idempotency_key,
                    reference_type,
                    reference_id,
                    expires_at,
                    provider_payload_json,
                    attempt_status,
                    calculated_for_date,
                    financial_snapshot_json,
                    snapshot_hash,
                    reconciliation_status
                FROM payments
                WHERE provider_payment_id = 'legacy-payment-1'
                """
            )
        ).mappings().one()

    for name in (
        "amount",
        "status",
        "confirmed_at",
        "amount_received",
        "provider_payment_id",
        "idempotency_key",
        "reference_type",
        "reference_id",
        "expires_at",
        "provider_payload_json",
    ):
        assert after[name] == before[name]

    assert after["attempt_status"] is None
    assert after["calculated_for_date"] is None
    assert after["financial_snapshot_json"] is None
    assert after["snapshot_hash"] is None
    assert after["reconciliation_status"] is None

    index_names = {
        index["name"]
        for index in inspect(engine).get_indexes("payments")
    }
    assert INDEX_NAME in index_names

    engine.dispose()



def test_sqlite_downgrade_and_reupgrade_preserve_legacy_payment(tmp_path):
    database = tmp_path / "a375b_round_trip.db"

    _alembic(database, "upgrade", PREVIOUS_REVISION)

    engine = create_engine(f"sqlite:///{database}")
    _insert_legacy_payment(engine)

    with engine.connect() as connection:
        original = connection.execute(
            text(
                """
                SELECT
                    amount,
                    status,
                    confirmed_at,
                    amount_received,
                    provider_payment_id,
                    idempotency_key,
                    reference_type,
                    reference_id,
                    expires_at,
                    provider_payload_json
                FROM payments
                WHERE provider_payment_id = 'legacy-payment-1'
                """
            )
        ).mappings().one()

    engine.dispose()

    _alembic(database, "upgrade", REVISION)

    engine = create_engine(f"sqlite:///{database}")
    upgraded_columns = {
        column["name"]
        for column in inspect(engine).get_columns("payments")
    }

    for name in (
        "attempt_status",
        "calculated_for_date",
        "financial_snapshot_json",
        "snapshot_hash",
        "reconciliation_status",
    ):
        assert name in upgraded_columns

    engine.dispose()

    _alembic(database, "downgrade", PREVIOUS_REVISION)

    engine = create_engine(f"sqlite:///{database}")

    downgraded_columns = {
        column["name"]
        for column in inspect(engine).get_columns("payments")
    }

    for name in (
        "attempt_status",
        "calculated_for_date",
        "financial_snapshot_json",
        "snapshot_hash",
        "reconciliation_status",
    ):
        assert name not in downgraded_columns

    with engine.connect() as connection:
        downgraded = connection.execute(
            text(
                """
                SELECT
                    amount,
                    status,
                    confirmed_at,
                    amount_received,
                    provider_payment_id,
                    idempotency_key,
                    reference_type,
                    reference_id,
                    expires_at,
                    provider_payload_json
                FROM payments
                WHERE provider_payment_id = 'legacy-payment-1'
                """
            )
        ).mappings().one()

        version = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()

    assert version == PREVIOUS_REVISION

    for name in original:
        assert downgraded[name] == original[name]

    engine.dispose()

    _alembic(database, "upgrade", REVISION)

    engine = create_engine(f"sqlite:///{database}")

    reupgraded_columns = {
        column["name"]
        for column in inspect(engine).get_columns("payments")
    }

    for name in (
        "attempt_status",
        "calculated_for_date",
        "financial_snapshot_json",
        "snapshot_hash",
        "reconciliation_status",
    ):
        assert name in reupgraded_columns

    with engine.connect() as connection:
        reupgraded = connection.execute(
            text(
                """
                SELECT
                    amount,
                    status,
                    confirmed_at,
                    amount_received,
                    provider_payment_id,
                    idempotency_key,
                    reference_type,
                    reference_id,
                    expires_at,
                    provider_payload_json,
                    attempt_status,
                    calculated_for_date,
                    financial_snapshot_json,
                    snapshot_hash,
                    reconciliation_status
                FROM payments
                WHERE provider_payment_id = 'legacy-payment-1'
                """
            )
        ).mappings().one()

        version = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()

    assert version == REVISION

    for name in original:
        assert reupgraded[name] == original[name]

    assert reupgraded["attempt_status"] is None
    assert reupgraded["calculated_for_date"] is None
    assert reupgraded["financial_snapshot_json"] is None
    assert reupgraded["snapshot_hash"] is None
    assert reupgraded["reconciliation_status"] is None

    engine.dispose()



def test_snapshot_storage_and_existing_expiry_round_trip():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    snapshot = {
        "snapshot_version": "payment_attempt_snapshot_v1",
        "calculated_for_date": "2026-09-21",
        "financial_timezone": "America/Belem",
        "components": {
            "principal": "10.00",
            "normal_interest": "20.00",
            "fixed_penalty": "10.00",
            "late_interest": "1.00",
        },
        "total_due": "41.00",
    }

    payment = Payment(
        provider="test_provider",
        provider_payment_id="payment-1",
        idempotency_key="idempotency-1",
        amount=Decimal("41.00"),
        status="PENDING",
        attempt_status="PENDING",
        calculated_for_date=date(2026, 9, 21),
        financial_snapshot_json=json.dumps(snapshot),
        snapshot_hash="a" * 64,
        reconciliation_status="STALE_OBLIGATION",
        expires_at=datetime(
            2026,
            9,
            30,
            12,
            0,
            tzinfo=timezone.utc,
        ),
    )
    session.add(payment)
    session.commit()

    stored = session.get(Payment, payment.id)

    assert json.loads(stored.financial_snapshot_json) == snapshot
    assert stored.attempt_status == "PENDING"
    assert stored.calculated_for_date == date(2026, 9, 21)
    assert stored.snapshot_hash == "a" * 64
    assert stored.reconciliation_status == "STALE_OBLIGATION"
    assert stored.expires_at is not None

    session.close()
    engine.dispose()


def test_partial_unique_index_only_applies_to_pending_attempts():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    first = Payment(
        provider="provider",
        provider_payment_id="payment-1",
        idempotency_key="idempotency-1",
        amount=Decimal("10.00"),
        status="PENDING",
        attempt_status="PENDING",
        reference_type="LOAN_INSTALLMENT",
        reference_id="42",
    )
    session.add(first)
    session.commit()

    duplicate_pending = Payment(
        provider="provider",
        provider_payment_id="payment-2",
        idempotency_key="idempotency-2",
        amount=Decimal("10.00"),
        status="PENDING",
        attempt_status="PENDING",
        reference_type="LOAN_INSTALLMENT",
        reference_id="42",
    )
    session.add(duplicate_pending)

    with pytest.raises(IntegrityError):
        session.commit()

    session.rollback()

    historical = Payment(
        provider="provider",
        provider_payment_id="payment-3",
        idempotency_key="idempotency-3",
        amount=Decimal("10.00"),
        status="PENDING",
        attempt_status=None,
        reference_type="LOAN_INSTALLMENT",
        reference_id="42",
    )
    session.add(historical)
    session.commit()

    session.close()
    engine.dispose()
