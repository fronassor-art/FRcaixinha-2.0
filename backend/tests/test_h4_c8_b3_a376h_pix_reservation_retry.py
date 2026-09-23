import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.api.loan_installment_payments import create_installment_pix
from app.core.pix_attempt_v1 import financial_date
from app.db.base import Base
from app.models import Group, Loan, LoanInstallment, Member, Payment, User
from app.services.loan_installment_pix_attempts import PENDING, EXPIRED, SUPERSEDED, PROVIDER_CREATE_UNKNOWN

engine=create_engine("sqlite:///:memory:", connect_args={"check_same_thread":False}, poolclass=StaticPool)
Session=sessionmaker(bind=engine)
Base.metadata.create_all(engine)

def seed(s, suffix):
    u=User(name="U"+suffix,email="u"+suffix+"@x.test",cpf="cpf"+suffix,password_hash="x")
    g=Group(name="G"+suffix); s.add_all([u,g]); s.flush()
    m=Member(user_id=u.id,group_id=g.id,status="ACTIVE"); s.add(m);s.flush()
    loan=Loan(member_id=m.id,principal=Decimal("100.00"),monthly_rate=Decimal("0.20"),installments=1,status="ACTIVE");s.add(loan);s.flush()
    inst=LoanInstallment(loan_id=loan.id,number=1,due_date=financial_date(datetime.now(timezone.utc)),principal=Decimal("100.00"),interest=Decimal("20.00"),amount=Decimal("120.00"),paid_amount=Decimal("0"),penalty_amount=Decimal("0"),paid_penalty_amount=Decimal("0"),status="OPEN")
    s.add(inst);s.commit();return u,inst

def response(n): return {"id":"pay-"+n,"order_id":"order-"+n,"status":"pending","qr_code":"qr-"+n,"qr_code_base64":"b64-"+n,"ticket_url":"https://x/"+n}

def test_tx1_reservation_is_durable_before_provider_call(monkeypatch):
    s=Session();u,inst=seed(s,"tx1"); seen={}
    async def provider(self, **kw):
        other=Session(); row=other.query(Payment).filter_by(reference_type="LOAN_INSTALLMENT",reference_id=str(inst.id)).one(); seen.update(id=row.id,pid=row.provider_payment_id,status=row.attempt_status,key=row.idempotency_key,date=row.calculated_for_date,snapshot=row.financial_snapshot_json,hash=row.snapshot_hash,expires=row.expires_at,amount=row.amount,external=kw["external_reference"],sent_key=kw["idempotency_key"],sent_amount=kw["amount"]);other.close();return response("tx1")
    monkeypatch.setattr("app.api.loan_installment_payments.MercadoPagoClient.create_pix_payment",provider)
    out=asyncio.run(create_installment_pix(inst.id,u,s))
    assert seen["id"]==out["payment_id"] and seen["pid"] is None and seen["status"]==PENDING
    assert all(seen[x] is not None for x in ("key","date","snapshot","hash","expires"))
    assert seen["sent_key"]==seen["key"] and seen["sent_amount"]==seen["amount"]==Decimal("120.00")
    assert seen["external"]==f"loan-installment-{inst.id}"

def test_tx2_binds_provider_response_to_original_reservation(monkeypatch):
    s=Session();u,inst=seed(s,"tx2"); ids=[]
    async def provider(self, **kw):
        row=Session().query(Payment).filter_by(reference_type="LOAN_INSTALLMENT",reference_id=str(inst.id)).one();ids.append(row.id);return response("tx2")
    monkeypatch.setattr("app.api.loan_installment_payments.MercadoPagoClient.create_pix_payment",provider)
    out=asyncio.run(create_installment_pix(inst.id,u,s)); rows=s.query(Payment).filter_by(reference_type="LOAN_INSTALLMENT",reference_id=str(inst.id)).all()
    assert len(rows)==1 and rows[0].id==ids[0]==out["payment_id"]
    assert (rows[0].provider_payment_id,rows[0].provider_order_id,rows[0].qr_code,rows[0].raw_status)==("pay-tx2","order-tx2","qr-tx2","pending")

def test_retry_after_ambiguous_provider_failure_reuses_same_reservation_and_key(monkeypatch):
    s=Session();u,inst=seed(s,"retry"); calls=[]
    async def provider(self, **kw):
        calls.append(kw);
        if len(calls)==1: raise TimeoutError("network timeout")
        return response("retry")
    monkeypatch.setattr("app.api.loan_installment_payments.MercadoPagoClient.create_pix_payment",provider)
    with pytest.raises(Exception): asyncio.run(create_installment_pix(inst.id,u,s))
    first=s.query(Payment).filter_by(reference_type="LOAN_INSTALLMENT",reference_id=str(inst.id)).one(); snap=(first.id,first.idempotency_key,first.financial_snapshot_json,first.snapshot_hash,first.calculated_for_date,first.expires_at)
    assert first.provider_payment_id is None and first.attempt_status==PENDING and first.reconciliation_status==PROVIDER_CREATE_UNKNOWN
    out=asyncio.run(create_installment_pix(inst.id,u,s)); rows=s.query(Payment).filter_by(reference_type="LOAN_INSTALLMENT",reference_id=str(inst.id)).all()
    assert len(rows)==1 and rows[0].id==snap[0]==out["payment_id"] and calls[0]["idempotency_key"]==calls[1]["idempotency_key"]==snap[1]
    assert rows[0].provider_payment_id=="pay-retry" and rows[0].reconciliation_status is None

def test_reuse_bound_pending_attempt_does_not_call_provider_again(monkeypatch):
    s=Session();u,inst=seed(s,"bound"); called=[]
    async def first(self, **kw): return response("bound")
    monkeypatch.setattr("app.api.loan_installment_payments.MercadoPagoClient.create_pix_payment",first); one=asyncio.run(create_installment_pix(inst.id,u,s))
    async def forbidden(self, **kw): called.append(kw); raise AssertionError("provider should not be called")
    monkeypatch.setattr("app.api.loan_installment_payments.MercadoPagoClient.create_pix_payment",forbidden); two=asyncio.run(create_installment_pix(inst.id,u,s))
    assert one["payment_id"]==two["payment_id"] and not called and s.query(Payment).count()>=1

@pytest.mark.parametrize("terminal",[EXPIRED,SUPERSEDED])
def test_new_attempt_after_terminal_gets_new_payment_and_idempotency_key(monkeypatch,terminal):
    s=Session();u,inst=seed(s,"term"+terminal); n=[]
    async def provider(self, **kw): n.append(kw);return response(str(len(n))+terminal)
    monkeypatch.setattr("app.api.loan_installment_payments.MercadoPagoClient.create_pix_payment",provider); first=asyncio.run(create_installment_pix(inst.id,u,s)); old=s.get(Payment,first["payment_id"]);old.attempt_status=terminal;s.commit(); second=asyncio.run(create_installment_pix(inst.id,u,s)); new=s.get(Payment,second["payment_id"])
    assert old.id!=new.id and old.attempt_status==terminal and new.idempotency_key!=old.idempotency_key
