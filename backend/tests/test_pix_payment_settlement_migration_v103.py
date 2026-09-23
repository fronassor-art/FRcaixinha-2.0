from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import Contribution, Group, Member, Payment, PaymentSettlement, User


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = BACKEND_ROOT / "alembic" / "versions" / "0079_pix_payment_settlement_v103.py"


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def _member(db):
    group = Group(name="PIX", max_installments=6)
    user = User(name="PIX", email="pix@example.test", cpf="123", password_hash="x")
    db.add_all([group, user])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id)
    db.add(member)
    db.flush()
    return member


def _payment(db, suffix="1"):
    payment = Payment(
        provider="mercado_pago",
        provider_payment_id=f"payment-{suffix}",
        idempotency_key=f"idempotency-{suffix}",
        amount=Decimal("50.00"),
        status="PENDING",
    )
    db.add(payment)
    db.flush()
    return payment


def test_0079_is_the_only_alembic_head():
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    assert tuple(ScriptDirectory.from_config(config).get_heads()) == ("0097_cycle_participation_a377b1",)


def test_migration_is_additive_and_declares_auditable_schema():
    migration = MIGRATION_PATH.read_text()

    assert 'down_revision = "0078_loan_simulation_confirmation_v102"' in migration
    assert migration.split("def downgrade():", 1)[0].count("op.create_table(") == 1
    assert '"payment_settlements"' in migration
    assert "receipt_version" in migration
    assert "receipt_hash" in migration
    assert "receipt_snapshot_json" in migration
    assert "op.execute(" not in migration
    assert "UPDATE " not in migration
    assert "DELETE " not in migration
    assert "op.alter_column(" not in migration


def test_models_expose_required_indexes_constraints_and_foreign_keys():
    _, engine = _session()
    settlement = PaymentSettlement.__table__

    assert {fk.column.table.name for fk in settlement.foreign_keys} == {
        "payments", "members", "contributions", "loan_installments", "agreement_installments", "webhook_events"
    }
    assert {constraint.name for constraint in settlement.constraints if constraint.name} >= {
        "ck_payment_settlements_nonnegative_amounts",
        "ck_payment_settlements_received_allocation",
        "ck_payment_settlements_applied_components",
        "ck_payment_settlements_single_obligation",
        "ck_payment_settlements_status_before",
        "ck_payment_settlements_status_after",
        "ck_payment_settlements_receipt_version",
    }
    assert {index.name for index in Payment.__table__.indexes} >= {
        "uq_payments_provider_pix_txid",
        "uq_payments_provider_end_to_end_id",
        "ix_payments_external_reference",
        "ix_payments_status_expires_at",
    }
    assert {index.name for index in Contribution.__table__.indexes} >= {
        "ix_contributions_status_due_date",
        "ix_contributions_member_status_due_date",
    }
    assert {index.name for index in settlement.indexes} >= {
        "ix_payment_settlements_member_confirmed",
        "ix_payment_settlements_contribution_id",
        "ix_payment_settlements_loan_installment_id",
        "ix_payment_settlements_agreement_installment_id",
        "ix_payment_settlements_webhook_event_id",
    }
    assert "payment_settlements" in inspect(engine).get_table_names()


def test_historical_rows_keep_new_fields_null_and_settlement_is_unique_per_payment():
    db, _ = _session()
    member = _member(db)
    historical_payment = _payment(db, "historical")
    historical_contribution = Contribution(
        member_id=member.id,
        competence=date(2026, 1, 1),
        amount=Decimal("50.00"),
        status="PENDING",
    )
    db.add(historical_contribution)
    db.commit()

    assert historical_payment.external_reference is None
    assert historical_payment.pix_txid is None
    assert historical_payment.end_to_end_id is None
    assert historical_payment.provider_payload_json is None
    assert historical_payment.confirmed_at is None
    assert historical_payment.expires_at is None
    assert historical_payment.amount_received is None
    assert historical_contribution.due_date is None
    assert historical_contribution.paid_amount is None
    assert historical_contribution.paid_at is None

    settlement = PaymentSettlement(
        payment_id=historical_payment.id,
        member_id=member.id,
        obligation_type="CONTRIBUTION",
        contribution_id=historical_contribution.id,
        amount_received=Decimal("50.00"),
        amount_applied=Decimal("50.00"),
        principal_applied=Decimal("50.00"),
        interest_applied=Decimal("0.00"),
        penalty_applied=Decimal("0.00"),
        excess_amount=Decimal("0.00"),
        obligation_status_before="PENDING",
        obligation_status_after="PAID",
        confirmed_at=datetime.now(timezone.utc),
        confirmation_source="WEBHOOK",
        receipt_number="PIX-SETTLEMENT-1",
        receipt_version="v1",
        receipt_snapshot_json='{"receipt_version":"v1"}',
        receipt_hash="a" * 64,
    )
    db.add(settlement)
    db.commit()

    duplicate = PaymentSettlement(
        payment_id=historical_payment.id,
        member_id=member.id,
        obligation_type="CONTRIBUTION",
        contribution_id=historical_contribution.id,
        amount_received=Decimal("50.00"),
        amount_applied=Decimal("50.00"),
        principal_applied=Decimal("50.00"),
        interest_applied=Decimal("0.00"),
        penalty_applied=Decimal("0.00"),
        excess_amount=Decimal("0.00"),
        obligation_status_before="PENDING",
        obligation_status_after="PAID",
        confirmed_at=datetime.now(timezone.utc),
        confirmation_source="WEBHOOK",
        receipt_number="PIX-SETTLEMENT-2",
        receipt_version="v1",
        receipt_snapshot_json='{"receipt_version":"v1"}',
        receipt_hash="b" * 64,
    )
    db.add(duplicate)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.close()
