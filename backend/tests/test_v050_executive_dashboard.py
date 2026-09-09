from datetime import date, timedelta
from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models import User, Group, Member, Loan, LoanInstallment, LedgerEntry, FinancialReconciliation, FinancialRiskAssessment, ExecutiveDashboardSnapshot
from app.services.executive_dashboard_v050 import build_executive_dashboard, persist_executive_dashboard

def db():
    e=create_engine('sqlite:///:memory:'); Base.metadata.create_all(e); return sessionmaker(bind=e)()

def seed():
    s=db(); u=User(name='A',email='a@x.com',cpf='1',password_hash='x'); s.add(u); s.flush()
    g=Group(name='G'); s.add(g); s.flush(); m=Member(user_id=u.id,group_id=g.id,declared_monthly_income=1000); s.add(m); s.flush()
    l=Loan(member_id=m.id,principal=500,monthly_rate=.1,installments=1,status='ACTIVE'); s.add(l); s.flush()
    i=LoanInstallment(loan_id=l.id,number=1,due_date=date.today()-timedelta(days=10),principal=500,interest=50,amount=550); s.add(i)
    s.add(LedgerEntry(account='cash',direction='CREDIT',amount=1000,reference_type='TEST',reference_id='1'))
    s.commit(); return s

def test_dashboard_consolidates_cash_loans_and_collections():
    s=seed(); r=build_executive_dashboard(s); assert r['schema']=='v0.50'; assert r['cash']['logical_balance']=='1000.00'; assert r['loans']['active_or_restructured']==1; assert r['collections']['overdue_installments']==1; assert 'DELINQUENCY' in r['risk_flags']

def test_dashboard_attention_for_reconciliation_missing():
    s=seed(); r=build_executive_dashboard(s); assert r['status']=='ATTENTION'; assert 'RECONCILIATION' in r['risk_flags']

def test_dashboard_snapshot_is_hashed_and_idempotent_by_date():
    s=seed(); row,a=persist_executive_dashboard(s,1,date.today()); s.commit(); first=row.snapshot_hash
    row2,b=persist_executive_dashboard(s,1,date.today()); s.commit(); assert row2.id==row.id and row2.snapshot_hash==first; assert s.query(ExecutiveDashboardSnapshot).count()==1
