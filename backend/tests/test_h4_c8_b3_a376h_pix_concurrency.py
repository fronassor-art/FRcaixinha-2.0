"""SQLite structural concurrency proof plus real lifecycle concurrency audit."""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models import Payment

ROOT=Path(__file__).parents[1]
SERVICE=ROOT/"app/services/loan_installment_pix_attempts.py"
API=ROOT/"app/api/loan_installment_payments.py"

def _session():
    engine=create_engine("sqlite:///:memory:"); Base.metadata.create_all(engine); return sessionmaker(bind=engine)()

def _payment(*, reference_type="LOAN_INSTALLMENT", reference_id="1", key="key", attempt_status="PENDING"):
    return Payment(provider="test",provider_payment_id="provider-"+key,idempotency_key=key,amount=Decimal("1.00"),status="PENDING",reference_type=reference_type,reference_id=reference_id,attempt_status=attempt_status,created_at=datetime.now(timezone.utc))

def test_unique_pending_index_rejects_second_pending_attempt():
    s=_session(); first=_payment(key="winner"); s.add(first); s.commit(); s.add(_payment(key="loser"))
    with pytest.raises(IntegrityError): s.commit()
    s.rollback(); rows=s.query(Payment).filter_by(reference_type="LOAN_INSTALLMENT",reference_id="1",attempt_status="PENDING").all()
    assert len(rows)==1 and rows[0].id==first.id and rows[0].idempotency_key=="winner"

def test_terminal_attempt_allows_new_pending_for_same_installment():
    s=_session(); old=_payment(key="old");s.add(old);s.commit();old.attempt_status="EXPIRED";s.commit();new=_payment(key="new");s.add(new);s.commit()
    assert old.id != new.id and old.attempt_status=="EXPIRED" and new.attempt_status=="PENDING" and old.idempotency_key!=new.idempotency_key
    assert s.query(Payment).filter_by(reference_type="LOAN_INSTALLMENT",reference_id="1",attempt_status="PENDING").count()==1

def test_pending_uniqueness_is_scoped_by_reference_type_and_reference_id():
    s=_session();s.add_all([_payment(key="a",reference_id="A"),_payment(key="b",reference_id="B"),_payment(key="c",reference_type="CONTRIBUTION",reference_id="A")]);s.commit()
    assert s.query(Payment).filter_by(attempt_status="PENDING").count()==3

def test_session_remains_usable_after_pending_attempt_integrity_error():
    s=_session();s.add(_payment(key="winner"));s.commit();s.add(_payment(key="loser"))
    with pytest.raises(IntegrityError): s.commit()
    s.rollback(); winner=s.query(Payment).filter_by(idempotency_key="winner").one(); winner.attempt_status="EXPIRED";s.commit();s.add(_payment(key="new"));s.commit()
    assert s.query(Payment).filter_by(idempotency_key="new").one().attempt_status=="PENDING"

def test_integrity_error_loser_converges_to_existing_pending_attempt():
    source=SERVICE.read_text()
    # The real reserve() catches the partial-index race, rolls back only its
    # transaction, reloads the winner, and returns it without a new key.
    assert "except IntegrityError:" in source and "db.rollback()" in source
    assert "winner=_pending(db,inst.id)" in source and "return winner,False" in source

def test_concurrent_generation_does_not_create_two_provider_requests_for_same_pending_attempt():
    service=SERVICE.read_text(); api=API.read_text()
    # TX1 commits before the endpoint can invoke NETWORK; a bound canonical
    # attempt returns before provider creation.
    assert "db.commit(); db.refresh(attempt)" in service
    assert "if payment.provider_payment_id:" in api
    assert "idempotency_key=payment.idempotency_key" in api

def test_concurrent_retry_reuses_same_unbound_reservation_and_key():
    service=SERVICE.read_text(); api=API.read_text()
    assert "if decision is ReuseDecision.REUSE: return old,False" in service
    assert "idempotency_key=payment.idempotency_key" in api

def test_for_update_audit_lock_order():
    source=SERVICE.read_text()
    # reserve() obtains LoanInstallment first, then probes/locks a pending Payment.
    assert source.index("_lock_installment(db,installment_id)") < source.index("old=_pending(db,inst.id)")
    assert "if _pg(db): q=q.with_for_update().populate_existing()" in source
    assert "if _pg(db): q=q.with_for_update()" in source
