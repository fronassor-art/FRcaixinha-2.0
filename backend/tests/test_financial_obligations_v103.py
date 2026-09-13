from datetime import date,timedelta
from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models import User,Group,Member,Contribution,Loan,LoanInstallment
from app.services.financial_obligations import contribution_item,installment_item,member_obligations

def db():
 e=create_engine('sqlite:///:memory:'); Base.metadata.create_all(e); return sessionmaker(bind=e)()
def member(s):
 g=Group(name='G',max_installments=6);u=User(name='U',email='u@x',cpf='1',password_hash='x');s.add_all([g,u]);s.flush();m=Member(user_id=u.id,group_id=g.id);s.add(m);s.flush();return m
def test_contribution_states_and_overdue_partial():
 s=db();m=member(s); today=date.today(); rows=[Contribution(member_id=m.id,competence=date(2026,i,1),amount=10,due_date=today+timedelta(days=1),paid_amount=0,status='PENDING') for i in range(1,5)];rows[1].paid_amount=4;rows[1].status='PARTIAL';rows[2].due_date=today-timedelta(days=2);rows[3].paid_amount=10;rows[3].status='PAID';s.add_all(rows);s.flush(); assert [contribution_item(s,x)['financial_status'] for x in rows]==['PENDING','PARTIAL','OVERDUE','PAID'];assert contribution_item(s,rows[2])['days_overdue']==2
def test_installment_states_and_member_isolation():
 s=db();m=member(s);l=Loan(member_id=m.id,principal=100,monthly_rate=.2,installments=4,status='ACTIVE');s.add(l);s.flush(); today=date.today(); a=LoanInstallment(loan_id=l.id,number=1,due_date=today+timedelta(days=1),principal=10,interest=2,amount=12,paid_amount=0,status='OPEN');b=LoanInstallment(loan_id=l.id,number=2,due_date=today+timedelta(days=1),principal=10,interest=2,amount=12,paid_amount=4,status='PARTIAL');c=LoanInstallment(loan_id=l.id,number=3,due_date=today-timedelta(days=1),principal=10,interest=2,amount=12,paid_amount=4,status='PARTIAL');d=LoanInstallment(loan_id=l.id,number=4,due_date=today-timedelta(days=1),principal=10,interest=2,amount=12,paid_amount=12,status='PAID');s.add_all([a,b,c,d]);s.flush();assert [installment_item(s,x)['financial_status'] for x in [a,b,c,d]]==['PENDING','PARTIAL','OVERDUE','PAID']; assert len(member_obligations(s,m.id))==4
