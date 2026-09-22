from datetime import date, datetime, timedelta, timezone
import asyncio
from decimal import Decimal
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.api import loan_installment_payments as loan_api
from app.api import payments as payments_api
from app.core.config import settings
from app.db.base import Base
from app.models import Group, LedgerEntry, Loan, LoanInstallment, LoanLateChargeEvent, Member, MemberFinancialEntry, Payment, PaymentReversal, PaymentSettlement, User
from app.services.member_financial import get_member_financial_position

def _db():
    e=create_engine("sqlite:///:memory:");Base.metadata.create_all(e);return sessionmaker(bind=e)()
def _seed(s):
    u=User(name="Read",email="read@x.test",cpf="cpf-read",password_hash="x");g=Group(name="Read");s.add_all([u,g]);s.flush();m=Member(user_id=u.id,group_id=g.id,status="ACTIVE");s.add(m);s.flush();l=Loan(member_id=m.id,principal=Decimal("10"),monthly_rate=Decimal("0"),installments=1,status="ACTIVE");s.add(l);s.flush();i=LoanInstallment(loan_id=l.id,number=1,due_date=date.today()-timedelta(days=3),principal=Decimal("10"),interest=Decimal("0"),amount=Decimal("10"),paid_amount=Decimal("0"),penalty_amount=Decimal("0"),paid_penalty_amount=Decimal("0"),status="OPEN");s.add(i);s.flush();p=Payment(provider="test",provider_payment_id="read-payment",idempotency_key="read-key",amount=Decimal("10"),status="pending",reference_type="LOAN_INSTALLMENT",reference_id=str(i.id),attempt_status="PENDING",expires_at=datetime.now(timezone.utc)-timedelta(days=1));s.add(p);s.commit();return u,m,i,p
def _state(s,i,p,m):
    position = get_member_financial_position(s, m)
    return (
        s.query(LoanLateChargeEvent).count(),
        s.query(PaymentSettlement).count(),
        s.query(LedgerEntry).count(),
        s.query(MemberFinancialEntry).count(),
        s.query(PaymentReversal).count(),
        s.query(Payment).filter_by(reference_type="LOAN_INSTALLMENT", reference_id=str(i.id)).count(),
        position["own_balance"],
        position["committed_balance"],
        position["available_balance"],
        i.principal,
        i.paid_amount,
        i.paid_penalty_amount,
        p.attempt_status,
        p.reconciliation_status,
    )

def test_payment_read_does_not_materialize_financial_side_effects(monkeypatch):
    s=_db();u,m,i,p=_seed(s);before=_state(s,i,p,m);monkeypatch.setattr(settings,"loan_late_charge_effective_date",date.today()-timedelta(days=5));out=asyncio.run(payments_api.payment_status(p.id,u,s));s.refresh(m);s.refresh(i);s.refresh(p)
    assert out["payment_id"]==p.id and _state(s,i,p,m)==before

def test_get_does_not_expire_pending_pix_attempt():
    s=_db();u,m,i,p=_seed(s);loan_api.installment_payment(i.id,u,s);s.refresh(p)
    assert p.attempt_status=="PENDING" and s.query(PaymentSettlement).count()==0 and s.query(Payment).filter_by(reference_type="LOAN_INSTALLMENT",reference_id=str(i.id)).count()==1

def test_get_does_not_materialize_late_charge_events(monkeypatch):
    s=_db();u,m,i,p=_seed(s);monkeypatch.setattr(settings,"loan_late_charge_effective_date",date.today()-timedelta(days=5));loan_api.installment_payment(i.id,u,s)
    assert s.query(LoanLateChargeEvent).count()==0

def test_read_path_does_not_call_financial_writers(monkeypatch):
    s=_db();u,m,i,p=_seed(s);calls=[]
    monkeypatch.setattr(payments_api,"settle_confirmed_pix_payment",lambda *a,**k:calls.append("settle"))
    loan_api.installment_payment(i.id,u,s);asyncio.run(payments_api.payment_status(p.id,u,s))
    assert calls==[]

def test_payment_read_returns_existing_state_without_mutation():
    s=_db();u,m,i,p=_seed(s);result=loan_api.installment_payment(i.id,u,s)
    assert result["payment"]["payment_id"]==p.id and result["payment"]["attempt_status"]=="PENDING" and result["remaining_amount"]=="10.00"
