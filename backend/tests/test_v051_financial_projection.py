from datetime import date
from decimal import Decimal
from app.models import Group, Member, User, Loan, LoanInstallment, LedgerEntry, Expense
from app.services.financial_projection_v051 import build_projection

def test_projection_schema_and_horizon():
    db=__import__("app.db.base",fromlist=["Base"])
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    e=create_engine("sqlite:///:memory:"); db.Base.metadata.create_all(e); db=sessionmaker(bind=e)()
    g=Group(name='G51',monthly_amount=Decimal('150'),months=12,due_day=10,active=True); db.add(g); db.flush()
    u=User(name='u51',email='u51@example.com',cpf='511',password_hash='x'); db.add(u); db.flush(); db.add(Member(user_id=u.id,group_id=g.id,status='ACTIVE')); db.commit()
    out=build_projection(db,date(2026,9,3),3,'BASE'); assert out['schema']=='v0.51'; assert len(out['projection'])==3

def test_negative_cash_attention():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.base import Base
    e=create_engine("sqlite:///:memory:"); Base.metadata.create_all(e); db=sessionmaker(bind=e)()
    db.add(LedgerEntry(account='x',direction='DEBIT',amount=Decimal('1000'),reference_type='T',reference_id='1')); db.commit()
    out=build_projection(db,date(2026,9,3),1,'CONSERVATIVE'); assert out['status']=='ATTENTION'

def test_scenarios_change_contributions():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.base import Base
    e=create_engine("sqlite:///:memory:"); Base.metadata.create_all(e); db=sessionmaker(bind=e)()
    g=Group(name='G52',monthly_amount=Decimal('200'),months=12,due_day=10,active=True); db.add(g); db.flush();
    for i in range(2):
        u=User(name=f'u52{i}',email=f'u52{i}@example.com',cpf=f'52{i}',password_hash='x'); db.add(u); db.flush(); db.add(Member(user_id=u.id,group_id=g.id,status='ACTIVE'))
    db.commit(); c=build_projection(db,date(2026,9,3),1,'CONSERVATIVE'); o=build_projection(db,date(2026,9,3),1,'OPTIMISTIC'); assert Decimal(o['projection'][0]['expected_contributions'])>Decimal(c['projection'][0]['expected_contributions'])

def test_invalid_scenario():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.base import Base
    e=create_engine("sqlite:///:memory:"); Base.metadata.create_all(e); db=sessionmaker(bind=e)()
    try: build_projection(db,date(2026,9,3),1,'X')
    except ValueError: return
    assert False
