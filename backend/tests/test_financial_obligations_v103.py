from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models import User,Group,Member,Contribution,Loan,LoanInstallment
from app.services.financial_obligations import contribution_item,installment_item,member_obligations
from app.services.late_charge_v1 import financial_civil_date
from app.api.admin import delinquency_summary

def db():
 e=create_engine('sqlite:///:memory:'); Base.metadata.create_all(e); return sessionmaker(bind=e)()
def member(s,suffix='1'):
 g=Group(name=f'G{suffix}',max_installments=6);u=User(name=f'U{suffix}',email=f'u{suffix}@x',cpf=suffix,password_hash='x');s.add_all([g,u]);s.flush();m=Member(user_id=u.id,group_id=g.id);s.add(m);s.flush();return m
def test_contribution_states_and_overdue_partial():
 s=db();m=member(s); as_of=datetime.now(timezone.utc); today=financial_civil_date(as_of); rows=[Contribution(member_id=m.id,competence=date(2026,i,1),amount=10,due_date=today+timedelta(days=1),paid_amount=0,status='PENDING') for i in range(1,5)];rows[1].paid_amount=4;rows[1].status='PARTIAL';rows[2].due_date=today-timedelta(days=2);rows[3].paid_amount=10;rows[3].status='PAID';s.add_all(rows);s.flush(); assert [contribution_item(s,x,as_of=as_of)['financial_status'] for x in rows]==['PENDING','PARTIAL','OVERDUE','PAID'];assert contribution_item(s,rows[2],as_of=as_of)['days_overdue']==2
def test_installment_states_and_member_isolation():
 s=db();m=member(s);l=Loan(member_id=m.id,principal=100,monthly_rate=.2,installments=4,status='ACTIVE');s.add(l);s.flush(); today=date.today(); a=LoanInstallment(loan_id=l.id,number=1,due_date=today+timedelta(days=1),principal=10,interest=2,amount=12,paid_amount=0,status='OPEN');b=LoanInstallment(loan_id=l.id,number=2,due_date=today+timedelta(days=1),principal=10,interest=2,amount=12,paid_amount=4,status='PARTIAL');c=LoanInstallment(loan_id=l.id,number=3,due_date=today-timedelta(days=1),principal=10,interest=2,amount=12,paid_amount=4,status='PARTIAL');d=LoanInstallment(loan_id=l.id,number=4,due_date=today-timedelta(days=1),principal=10,interest=2,amount=12,paid_amount=12,status='PAID');s.add_all([a,b,c,d]);s.flush();assert [installment_item(s,x)['financial_status'] for x in [a,b,c,d]]==['PENDING','PARTIAL','OVERDUE','PAID']; assert len(member_obligations(s,m.id))==4
 assert installment_item(s,b)['principal_outstanding']=='8.00'; assert installment_item(s,d)['principal_outstanding']=='0.00'

def test_delinquency_summary_counts_distinct_members_and_pending_components():
 s=db();first=member(s,'one');second=member(s,'two');today=date.today()
 s.add_all([Contribution(member_id=first.id,competence=date(2026,1,1),amount=10,due_date=today+timedelta(days=1),paid_amount=0,status='PENDING'),Contribution(member_id=second.id,competence=date(2026,2,1),amount=7,due_date=today+timedelta(days=1),paid_amount=2,status='PARTIAL')])
 loan=Loan(member_id=first.id,principal=10,monthly_rate=Decimal('0.20'),installments=1,status='ACTIVE');s.add(loan);s.flush()
 s.add(LoanInstallment(loan_id=loan.id,number=1,due_date=today+timedelta(days=1),principal=10,interest=2,amount=12,penalty_amount=5,paid_amount=0,status='OPEN'));s.flush()
 summary=delinquency_summary(admin=object(),db=s)
 assert summary['delinquent_members_count']==2
 assert summary['total_interest_outstanding']=='2.00'
 assert summary['total_penalty_outstanding']=='5.00'
 assert summary['total_outstanding']=='32.00'
 assert summary['counts']['PENDING']==2
 assert summary['counts']['PARTIAL']==1
