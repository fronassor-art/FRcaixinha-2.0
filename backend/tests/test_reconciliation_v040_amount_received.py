from datetime import date, datetime, timezone
from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models import Payment
from app.services.reconciliation_v040 import build_advanced_reconciliation

def test_advanced_reconciliation_prefers_amount_received_over_invoice_amount():
    engine=create_engine('sqlite:///:memory:'); Base.metadata.create_all(engine); db=sessionmaker(bind=engine)()
    payment=Payment(provider='mercado_pago',provider_payment_id='received-less',idempotency_key='received-less',amount=Decimal('100.00'),amount_received=Decimal('40.00'),status='approved',ledger_posted_at=datetime.now(timezone.utc))
    db.add(payment); db.commit()
    snapshot=build_advanced_reconciliation(db,date.today())['snapshot']
    assert snapshot['approved_payments']=='40.00'
    assert snapshot['posted_payments']=='40.00'
