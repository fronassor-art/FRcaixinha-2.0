from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import Group, Member, MemberFinancialAccount, MemberFinancialEntry, User
from app.services.member_financial import (
    add_member_financial_entry,
    get_member_financial_position,
    lock_member_financial_account,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        user = User(
            name="Mutex Test",
            email="mutex@example.com",
            cpf="11111111111",
            password_hash="test",
        )
        group = Group(name="Mutex Group")
        session.add_all([user, group])
        session.flush()
        member = Member(user_id=user.id, group_id=group.id)
        session.add(member)
        session.flush()
        yield session, member
    finally:
        session.close()
        engine.dispose()


def test_mutex_helper_creates_and_reuses_one_account(db):
    session, member = db

    locked_member, first = lock_member_financial_account(session, member)
    _, second = lock_member_financial_account(session, locked_member.id)

    assert locked_member.id == member.id
    assert first.id == second.id
    assert session.query(MemberFinancialAccount).filter_by(member_id=member.id).count() == 1


def test_mutex_entry_rollback_removes_entry_and_new_account(db):
    session, member = db

    add_member_financial_entry(
        session,
        member,
        entry_type="CONTRIBUTION",
        direction="CREDIT",
        amount=Decimal("150.00"),
        reference_type="CONTRIBUTION",
        reference_id="rollback-mutex",
    )
    assert get_member_financial_position(session, member)["own_balance"] == Decimal("150.00")

    session.rollback()

    assert session.query(MemberFinancialAccount).filter_by(member_id=member.id).count() == 0
    assert session.query(MemberFinancialEntry).count() == 0
    assert get_member_financial_position(session, member)["own_balance"] == Decimal("0.00")
