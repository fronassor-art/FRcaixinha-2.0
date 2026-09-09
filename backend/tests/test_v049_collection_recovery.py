from datetime import date, timedelta
from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models import User, Group, Member, Loan, LoanInstallment, CollectionCase
from app.services.collection_recovery_v049 import sync_cases, create_promise, collection_recovery_summary

def db():
 e=create_engine('sqlite:///:memory:'); Base.metadata.create_all(e); return sessionmaker(bind=e)()
def seed(due_days=10):
 s=db(); u=User(name='A',email='a@x.com',cpf='1',password_hash='x'); s.add(u); s.flush(); g=Group(name='G'); s.add(g); s.flush(); m=Member(user_id=u.id,group_id=g.id); s.add(m); s.flush(); l=Loan(member_id=m.id,principal=100,monthly_rate=.1,installments=1,status='ACTIVE'); s.add(l); s.flush(); i=LoanInstallment(loan_id=l.id,number=1,due_date=date.today()-timedelta(days=due_days),principal=100,interest=10,amount=110); s.add(i); s.commit(); return s,m,l

def test_sync_opens_and_escalates_case():
 s,m,l=seed(10); r=sync_cases(s); s.commit(); assert r['cases_opened']==1; c=s.query(CollectionCase).one(); assert c.stage=='INTENSIVE'
def test_promise_requires_open_case():
 s,m,l=seed(10); sync_cases(s); s.commit(); c=s.query(CollectionCase).one(); p=create_promise(s,c.id,1,50,date.today()+timedelta(days=2),'ok'); s.commit(); assert p.status=='PENDING'
def test_summary_counts_cases_and_promises():
 s,m,l=seed(40); sync_cases(s); s.commit(); c=s.query(CollectionCase).one(); create_promise(s,c.id,1,20,date.today()); s.commit(); x=collection_recovery_summary(s); assert x['open_cases']==1 and x['pending_promises']==1 and x['promises_due_or_late']==1
