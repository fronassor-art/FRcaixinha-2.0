import pytest
import asyncio
from decimal import Decimal
from datetime import date, datetime, timezone
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.api import payments as api
from app.db.base import Base
from app.models import Group, Loan, LoanInstallment, Member, Payment, PaymentReversal, PaymentSettlement, User, WebhookEvent
from app.core.pix_attempt_v1 import build_loan_installment_snapshot, canonical_json, financial_date, local_expiry_utc, snapshot_hash
from app.services.loan_installment_pix_attempts import _utc as normalize_utc

class Request:
    headers={}
    query_params={"data.id":"provider-1"}
    async def json(self): return {"id":"event-temporary-1","type":"payment","data":{"id":"provider-1"}}

def test_temporary_provider_failure_keeps_webhook_retryable(monkeypatch):
    engine=create_engine("sqlite:///:memory:");Base.metadata.create_all(engine);s=sessionmaker(bind=engine)()
    p=Payment(provider="mercado_pago",provider_payment_id="provider-1",provider_order_id="order-1",idempotency_key="key-1",amount=Decimal("1.00"),status="pending")
    s.add(p);s.commit()
    monkeypatch.setattr(api,"validate_mercado_pago_signature",lambda *a,**k: True)
    async def fail(self, order_id): raise TimeoutError("temporary")
    monkeypatch.setattr(api.MercadoPagoClient,"get_order",fail)
    try: asyncio.run(api.mercado_pago_webhook(Request(),s))
    except Exception: pass
    event=s.query(WebhookEvent).filter_by(event_id="event-temporary-1").one()
    assert event.processed is False
    assert s.query(Payment).filter_by(id=p.id).one().provider_payment_id=="provider-1"


class EventRequest:
    headers={}
    def __init__(self,event_id,payment_id="provider-1"): self.event_id=event_id; self.query_params={"data.id":payment_id}; self.payment_id=payment_id
    async def json(self): return {"id":self.event_id,"type":"payment","data":{"id":self.payment_id}}

def _db():
    engine=create_engine("sqlite:///:memory:");Base.metadata.create_all(engine);return sessionmaker(bind=engine)()
def _payment(s, provider_id="provider-1", reference_type=None):
    p=Payment(provider="mercado_pago",provider_payment_id=provider_id,provider_order_id="order-"+provider_id,idempotency_key="key-"+provider_id,amount=Decimal("10.00"),status="pending",reference_type=reference_type);s.add(p);s.commit();return p
def _remote(provider_id,status="pending",**extra):
    payment={"id":provider_id,"status":status};payment.update(extra);return {"id":"order-"+provider_id,"status":status,"transactions":{"payments":[payment]}}

def test_new_webhook_event_is_processed_once(monkeypatch):
    s=_db();_payment(s);monkeypatch.setattr(api,"validate_mercado_pago_signature",lambda *a,**k:True)
    async def order(self,oid): return _remote("provider-1")
    monkeypatch.setattr(api.MercadoPagoClient,"get_order",order);out=asyncio.run(api.mercado_pago_webhook(EventRequest("event-new"),s))
    event=s.query(WebhookEvent).filter_by(event_id="event-new").one();assert out["received"] and event.provider=="mercado_pago" and event.event_type=="payment" and event.processed is True and s.query(WebhookEvent).count()==1

def test_processed_webhook_is_idempotent_duplicate(monkeypatch):
    s=_db();s.add(WebhookEvent(provider="mercado_pago",event_id="event-duplicate",event_type="payment",processed=True));s.commit();monkeypatch.setattr(api,"validate_mercado_pago_signature",lambda *a,**k:True)
    out=asyncio.run(api.mercado_pago_webhook(EventRequest("event-duplicate"),s));assert out=={"received":True,"duplicate":True} and s.query(WebhookEvent).count()==1

def test_unprocessed_webhook_is_reprocessed_not_discarded_as_duplicate(monkeypatch):
    s=_db();_payment(s);s.add(WebhookEvent(provider="mercado_pago",event_id="event-retry",event_type="payment",processed=False));s.commit();monkeypatch.setattr(api,"validate_mercado_pago_signature",lambda *a,**k:True)
    async def order(self,oid): return _remote("provider-1")
    monkeypatch.setattr(api.MercadoPagoClient,"get_order",order);out=asyncio.run(api.mercado_pago_webhook(EventRequest("event-retry"),s));assert "duplicate" not in out and s.query(WebhookEvent).filter_by(event_id="event-retry").one().processed is True and s.query(WebhookEvent).count()==1

def test_unknown_provider_payment_preserves_unprocessed_webhook_for_retry(monkeypatch):
    s=_db();monkeypatch.setattr(api,"validate_mercado_pago_signature",lambda *a,**k:True);out=asyncio.run(api.mercado_pago_webhook(EventRequest("event-unknown","unknown"),s))
    assert out["reconciliable"] is True and s.query(WebhookEvent).filter_by(event_id="event-unknown").one().processed is False and s.query(Payment).count()==0

def test_provider_evidence_is_preserved_on_correlated_webhook(monkeypatch):
    s=_db();p=_payment(s,"evidence","LOAN_INSTALLMENT");monkeypatch.setattr(api,"validate_mercado_pago_signature",lambda *a,**k:True)
    async def order(self,oid): return _remote("evidence","approved",transaction_amount="10.00",date_approved="2026-01-01T12:00:00Z")
    monkeypatch.setattr(api.MercadoPagoClient,"get_order",order);asyncio.run(api.mercado_pago_webhook(EventRequest("event-evidence","evidence"),s));p=s.get(Payment,p.id);assert p.status=="approved" and p.raw_status=="approved" and p.amount_received==Decimal("10.00") and p.provider_payload_json and p.confirmed_at is not None

def test_approved_without_trusted_confirmation_time_does_not_settle(monkeypatch):
    s=_db();p=_payment(s,"missing","LOAN_INSTALLMENT");calls=[];monkeypatch.setattr(api,"validate_mercado_pago_signature",lambda *a,**k:True)
    async def order(self,oid): return _remote("missing","approved",transaction_amount="10.00")
    monkeypatch.setattr(api.MercadoPagoClient,"get_order",order);monkeypatch.setattr(api,"settle_confirmed_pix_payment",lambda *a,**k:calls.append(k))
    asyncio.run(api.mercado_pago_webhook(EventRequest("event-missing","missing"),s));p=s.get(Payment,p.id);assert p.status=="approved" and not calls and p.reconciliation_status is not None


def _versioned_payment(s, provider_id="valid"):
    u=User(name="Webhook "+provider_id,email=provider_id+"@x.test",cpf="cpf-"+provider_id,password_hash="x");g=Group(name="Webhook "+provider_id);s.add_all([u,g]);s.flush();m=Member(user_id=u.id,group_id=g.id);s.add(m);s.flush();loan=Loan(member_id=m.id,principal=Decimal("10.00"),monthly_rate=Decimal("0"),installments=1,status="ACTIVE");s.add(loan);s.flush()
    inst=LoanInstallment(loan_id=loan.id,number=1,due_date=date(2026,1,1),principal=Decimal("10.00"),interest=Decimal("0.00"),amount=Decimal("10.00"),paid_amount=Decimal("0"),penalty_amount=Decimal("0"),paid_penalty_amount=Decimal("0"),status="OPEN");s.add(inst);s.flush()
    confirmed=datetime(2026,1,1,15,30,tzinfo=timezone.utc);day=financial_date(confirmed);snap=build_loan_installment_snapshot(loan_id=loan.id,installment_id=inst.id,installment_number=inst.number,calculated_for_date=day,principal_due=Decimal("10.00"),normal_price_interest_due=Decimal("0.00"),fixed_penalty_due=Decimal("0.00"),late_interest_due=Decimal("0.00"))
    p=Payment(provider="mercado_pago",provider_payment_id=provider_id,provider_order_id="order-"+provider_id,idempotency_key="key-"+provider_id,amount=Decimal("10.00"),status="pending",reference_type="LOAN_INSTALLMENT",reference_id=str(inst.id),attempt_status="PENDING",calculated_for_date=day,financial_snapshot_json=canonical_json(snap),snapshot_hash=snapshot_hash(snap),expires_at=local_expiry_utc(day));s.add(p);s.commit();return p,confirmed

def test_approved_webhook_passes_exact_provider_confirmation_time_to_settlement(monkeypatch):
    s=_db();p,confirmed=_versioned_payment(s,"exact"); seen=[];monkeypatch.setattr(api,"validate_mercado_pago_signature",lambda *a,**k:True)
    async def order(self,oid): return _remote("exact","approved",transaction_amount="10.00",date_approved="2026-01-01T15:30:00Z")
    def settle(*args,**kwargs): seen.append(kwargs);return SimpleNamespace()
    monkeypatch.setattr(api.MercadoPagoClient,"get_order",order);monkeypatch.setattr(api,"settle_confirmed_pix_payment",settle)
    asyncio.run(api.mercado_pago_webhook(EventRequest("event-exact","exact"),s));stored=s.get(Payment,p.id);assert len(seen)==1 and seen[0]["confirmed_at"]==confirmed and seen[0]["confirmed_at"].tzinfo is not None and normalize_utc(stored.confirmed_at)==confirmed

def test_valid_versioned_approved_webhook_settles_exactly_once(monkeypatch):
    s=_db();p,confirmed=_versioned_payment(s,"once");calls=[];monkeypatch.setattr(api,"validate_mercado_pago_signature",lambda *a,**k:True)
    async def order(self,oid): return _remote("once","approved",transaction_amount="10.00",date_approved="2026-01-01T15:30:00Z")
    def settle(*args,**kwargs): calls.append(kwargs);return SimpleNamespace()
    monkeypatch.setattr(api.MercadoPagoClient,"get_order",order);monkeypatch.setattr(api,"settle_confirmed_pix_payment",settle)
    first=asyncio.run(api.mercado_pago_webhook(EventRequest("event-once","once"),s));stored=s.get(Payment,p.id);assert first["received"] and stored.attempt_status=="APPROVED" and stored.reconciliation_status is None and len(calls)==1 and calls[0]["confirmed_at"]==confirmed and s.query(WebhookEvent).filter_by(event_id="event-once").one().processed
    duplicate=asyncio.run(api.mercado_pago_webhook(EventRequest("event-once","once"),s));assert duplicate["duplicate"] is True and len(calls)==1 and s.query(WebhookEvent).filter_by(event_id="event-once").count()==1

def test_webhook_settlement_failure_leaves_event_retryable_and_no_partial_financial_effects(monkeypatch):
    s=_db();p,_=_versioned_payment(s,"failure");monkeypatch.setattr(api,"validate_mercado_pago_signature",lambda *a,**k:True)
    async def order(self,oid): return _remote("failure","approved",transaction_amount="10.00",date_approved="2026-01-01T15:30:00Z")
    def fail(*args,**kwargs): raise RuntimeError("settlement failure")
    monkeypatch.setattr(api.MercadoPagoClient,"get_order",order);monkeypatch.setattr(api,"settle_confirmed_pix_payment",fail)
    with pytest.raises(RuntimeError): asyncio.run(api.mercado_pago_webhook(EventRequest("event-failure","failure"),s))
    s.rollback();event=s.query(WebhookEvent).filter_by(event_id="event-failure").one();assert event.processed is False and s.query(PaymentSettlement).count()==0


def _approved_fail_closed(monkeypatch,s,p,event,confirmed="2026-01-01T15:30:00Z"):
    monkeypatch.setattr(api,"validate_mercado_pago_signature",lambda *a,**k:True)
    async def order(self,oid): return _remote(p.provider_payment_id,"approved",transaction_amount="10.00",date_approved=confirmed)
    monkeypatch.setattr(api.MercadoPagoClient,"get_order",order);calls=[];monkeypatch.setattr(api,"settle_confirmed_pix_payment",lambda *a,**k:calls.append(k));asyncio.run(api.mercado_pago_webhook(EventRequest(event,p.provider_payment_id),s));return calls,s.get(Payment,p.id)

def test_approved_after_local_expiry_is_reconciled_without_settlement(monkeypatch):
    s=_db();p,_=_versioned_payment(s,"expired");p.expires_at=datetime(2026,1,1,12,tzinfo=timezone.utc);s.commit();calls,row=_approved_fail_closed(monkeypatch,s,p,"event-expired")
    assert not calls and row.attempt_status=="EXPIRED" and row.reconciliation_status=="STALE_EXPIRED" and row.status=="approved" and row.amount_received==Decimal("10.00") and row.confirmed_at is not None

def test_approved_superseded_attempt_does_not_resurrect_or_settle(monkeypatch):
    s=_db();p,_=_versioned_payment(s,"superseded");p.attempt_status="SUPERSEDED";s.commit();calls,row=_approved_fail_closed(monkeypatch,s,p,"event-superseded")
    assert not calls and row.attempt_status=="SUPERSEDED" and row.reconciliation_status=="STALE_SUPERSEDED" and row.provider_payload_json

def test_corrupted_snapshot_hash_fails_closed(monkeypatch):
    s=_db();p,_=_versioned_payment(s,"hash");p.snapshot_hash="bad";original=p.financial_snapshot_json;s.commit();calls,row=_approved_fail_closed(monkeypatch,s,p,"event-hash")
    assert not calls and row.reconciliation_status=="SNAPSHOT_MISMATCH" and row.snapshot_hash=="bad" and row.financial_snapshot_json==original

def test_invalid_snapshot_json_fails_closed(monkeypatch):
    s=_db();p,_=_versioned_payment(s,"json");p.financial_snapshot_json="{";s.commit();calls,row=_approved_fail_closed(monkeypatch,s,p,"event-json")
    assert not calls and row.reconciliation_status=="SNAPSHOT_MISMATCH" and row.status=="approved" and row.provider_payload_json

def test_snapshot_reference_mismatch_fails_closed(monkeypatch):
    s=_db();p,_=_versioned_payment(s,"ref");snap=__import__("json").loads(p.financial_snapshot_json);snap["reference_id"]="other";p.financial_snapshot_json=canonical_json(snap);p.snapshot_hash=snapshot_hash(snap);s.commit();calls,row=_approved_fail_closed(monkeypatch,s,p,"event-ref")
    assert not calls and row.reconciliation_status=="SNAPSHOT_MISMATCH" and row.financial_snapshot_json==canonical_json(snap)

def test_legacy_loan_pix_approved_is_legacy_unverified(monkeypatch):
    s=_db();p=_payment(s,"legacy","LOAN_INSTALLMENT");calls,row=_approved_fail_closed(monkeypatch,s,p,"event-legacy")
    assert not calls and row.reconciliation_status=="LEGACY_UNVERIFIED" and row.attempt_status is None and row.confirmed_at is not None

def test_terminal_attempt_never_resurrects_on_late_provider_approval(monkeypatch):
    for terminal in ("EXPIRED","SUPERSEDED","CANCELLED","FAILED"):
        s=_db();p,_=_versioned_payment(s,"terminal-"+terminal);p.attempt_status=terminal;s.commit();calls,row=_approved_fail_closed(monkeypatch,s,p,"event-terminal-"+terminal)
        assert not calls and row.attempt_status==terminal and row.status=="approved"

@pytest.mark.parametrize("status",["refunded","charged_back"])
def test_refund_or_chargeback_provider_status_never_auto_reverses_financial_settlement(monkeypatch,status):
    s=_db();p=_payment(s,status);monkeypatch.setattr(api,"validate_mercado_pago_signature",lambda *a,**k:True)
    async def order(self,oid): return _remote(status,status)
    monkeypatch.setattr(api.MercadoPagoClient,"get_order",order);asyncio.run(api.mercado_pago_webhook(EventRequest("event-"+status,status),s));row=s.get(Payment,p.id)
    assert row.status==status and row.reconciliation_status is not None and s.query(PaymentReversal).count()==0


def test_snapshot_calculated_for_date_mismatch_fails_closed(monkeypatch):
    s=_db();p,confirmed=_versioned_payment(s,"date-mismatch");original_json=p.financial_snapshot_json;original_hash=p.snapshot_hash
    # Snapshot remains canonical/integral for its own date; only the persisted
    # attempt date diverges from provider financial date and snapshot date.
    p.calculated_for_date=date(2025,12,31);s.commit();calls,row=_approved_fail_closed(monkeypatch,s,p,"event-date-mismatch")
    assert not calls and row.reconciliation_status=="SNAPSHOT_MISMATCH" and row.attempt_status=="PENDING"
    assert row.financial_snapshot_json==original_json and row.snapshot_hash==original_hash and row.calculated_for_date==date(2025,12,31) and normalize_utc(row.confirmed_at)==confirmed

def test_current_obligation_mismatch_requires_reconciliation(monkeypatch):
    s=_db();p,_=_versioned_payment(s,"obligation-mismatch");original_json=p.financial_snapshot_json;original_hash=p.snapshot_hash
    # A prior authoritative settlement is the real mechanism that reduces an
    # installment obligation; inject its resulting component row before the
    # stale webhook is evaluated.
    inst=s.get(LoanInstallment,int(p.reference_id)); prior=Payment(provider="test",provider_payment_id="prior-obligation",idempotency_key="prior-obligation",amount=Decimal("1.00"),status="approved",reference_type="LOAN_INSTALLMENT",reference_id=str(inst.id));s.add(prior);s.flush()
    settlement=PaymentSettlement(payment_id=prior.id,member_id=s.get(Loan,inst.loan_id).member_id,obligation_type="LOAN_INSTALLMENT",loan_installment_id=inst.id,amount_received=Decimal("1.00"),amount_applied=Decimal("1.00"),principal_applied=Decimal("1.00"),interest_applied=Decimal("0"),penalty_applied=Decimal("0"),excess_amount=Decimal("0"),obligation_status_before="OPEN",obligation_status_after="PARTIAL",confirmed_at=datetime(2026,1,1,12,tzinfo=timezone.utc),confirmation_source="TEST",receipt_number="prior-obligation",receipt_version="v1",receipt_snapshot_json="{}",receipt_hash="prior")
    s.add(settlement);s.commit();calls,row=_approved_fail_closed(monkeypatch,s,p,"event-obligation-mismatch")
    assert not calls and row.reconciliation_status=="SNAPSHOT_MISMATCH" and row.financial_snapshot_json==original_json and row.snapshot_hash==original_hash
