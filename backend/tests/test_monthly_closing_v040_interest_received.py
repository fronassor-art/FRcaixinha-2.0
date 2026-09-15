from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.services import monthly_closing_v040


def test_closing_persists_only_interest_received_from_reconciliation(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    reconciliation = {
        "status": "PASS",
        "snapshot_hash": "a" * 64,
        "snapshot": {
            "schema": "v0.40",
            "competence": "2026-09-01",
            "period_end": "2026-09-30",
            "contributions_paid": "150.00",
            "expenses_posted": "10.00",
            "interest_received": "20.00",
            "loan_payments_ledger": "50.00",
            "agreement_payments_ledger": "30.00",
            "ledger_net": "190.00",
        },
    }

    monkeypatch.setattr(
        monthly_closing_v040,
        "build_advanced_reconciliation",
        lambda db, competence: reconciliation,
    )

    closing, snapshot, _ = monthly_closing_v040.close_month_v040(
        db,
        date(2026, 9, 1),
        admin_id=1,
    )

    assert snapshot["interest_received"] == "20.00"
    assert closing.total_interest_received == Decimal("20.00")

    db.close()
