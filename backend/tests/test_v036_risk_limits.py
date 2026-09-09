from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models import Group, User, Member, Loan, LedgerEntry
from app.services.risk_v036 import evaluate_release


def setup_db():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed(session, cash='1000.00'):
    user = User(name='A', email='a@example.com', cpf='1', password_hash='x')
    session.add(user); session.flush()
    group = Group(name='G', min_cash_reserve=Decimal('200.00'), max_member_exposure=Decimal('600.00'), max_global_exposure=Decimal('900.00'), max_exposure_ratio=Decimal('0.80'))
    session.add(group); session.flush()
    member = Member(user_id=user.id, group_id=group.id)
    session.add(member); session.flush()
    session.add(LedgerEntry(account='CAIXINHA', direction='CREDIT', amount=Decimal(cash), reference_type='TEST', reference_id='cash'))
    session.flush()
    loan = Loan(member_id=member.id, principal=Decimal('300.00'), monthly_rate=Decimal('0.10'), installments=3, status='APPROVED')
    session.add(loan); session.commit()
    return group, member, loan


def test_release_passes_when_all_limits_hold():
    db = setup_db(); group, member, loan = seed(db)
    r = evaluate_release(db, loan, group)
    assert r['status'] == 'PASS'
    assert r['cash_after_release'] == Decimal('700.00')
    assert r['member_exposure_after'] == Decimal('300.00')


def test_release_blocks_minimum_reserve():
    db = setup_db(); group, member, loan = seed(db, cash='400.00')
    r = evaluate_release(db, loan, group)
    assert r['status'] == 'BLOCKED'
    assert any('Saldo mínimo' in x for x in r['errors'])


def test_release_blocks_member_limit():
    db = setup_db(); group, member, loan = seed(db, cash='2000.00')
    existing = Loan(member_id=member.id, principal=Decimal('400.00'), monthly_rate=Decimal('0.10'), installments=3, status='ACTIVE')
    db.add(existing); db.commit()
    r = evaluate_release(db, loan, group)
    assert r['status'] == 'BLOCKED'
    assert any('participante' in x for x in r['errors'])


def test_release_blocks_global_limit():
    db = setup_db(); group, member, loan = seed(db, cash='2000.00')
    existing = Loan(member_id=member.id, principal=Decimal('700.00'), monthly_rate=Decimal('0.10'), installments=3, status='ACTIVE')
    db.add(existing); db.commit()
    r = evaluate_release(db, loan, group)
    assert r['status'] == 'BLOCKED'
    assert any('global' in x for x in r['errors'])
