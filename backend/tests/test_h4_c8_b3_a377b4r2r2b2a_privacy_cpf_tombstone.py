"""A3.77B4-R4.2-R2B2A: privacy CPF tombstone contract tests."""

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.cpf import CPFValidationError, normalize_cpf
from app.db.base import Base
from app.models import DataAccessLog, Group, Member, User, UserSession
from app.services.privacy_v045 import _privacy_cpf_tombstone, anonymize_user


MAX_POSTGRES_INTEGER_ID = 2_147_483_647
ORIGINAL_MEMBER_CPF = "52998224725"
ORIGINAL_ADMIN_CPF = "11144477735"


@pytest.mark.parametrize(
    ("user_id", "expected"),
    [
        (1, "ANON0000000001"),
        (42, "ANON0000000042"),
        (MAX_POSTGRES_INTEGER_ID, "ANON2147483647"),
    ],
)
def test_tombstone_format_is_bounded_and_id_only(user_id, expected):
    tombstone = _privacy_cpf_tombstone(user_id)

    assert tombstone == expected
    assert len(tombstone) <= 14
    assert _privacy_cpf_tombstone(user_id) == tombstone
    with pytest.raises(CPFValidationError):
        normalize_cpf(tombstone)


def test_distinct_supported_ids_have_distinct_tombstones():
    ids = (1, 2, 42, 999_999_999, MAX_POSTGRES_INTEGER_ID)
    tombstones = [_privacy_cpf_tombstone(user_id) for user_id in ids]

    assert len(tombstones) == len(set(tombstones))
    assert all(len(value) <= 14 for value in tombstones)


@pytest.mark.parametrize("user_id", [0, -1, MAX_POSTGRES_INTEGER_ID + 1, True])
def test_tombstone_rejects_ids_outside_positive_postgres_integer_range(user_id):
    with pytest.raises(ValueError, match="cannot be represented") as caught:
        _privacy_cpf_tombstone(user_id)
    assert str(user_id) not in str(caught.value)


@pytest.fixture()
def privacy_db(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'privacy-tombstone.sqlite'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        member_user = User(
            id=1,
            name="Synthetic Member Name",
            email="synthetic-member@example.com",
            cpf=ORIGINAL_MEMBER_CPF,
            phone="+15555550101",
            password_hash="unused",
        )
        admin_user = User(
            id=2,
            name="Synthetic Admin Name",
            email="synthetic-admin@example.com",
            cpf=ORIGINAL_ADMIN_CPF,
            phone="+15555550102",
            password_hash="unused",
        )
        db.add_all([member_user, admin_user, Group(id=1, name="Privacy test group")])
        db.flush()
        member = Member(id=1, user_id=member_user.id, group_id=1, status="ACTIVE")
        db.add(member)
        now = datetime.now(timezone.utc)
        db.add_all([
            UserSession(
                user_id=member_user.id,
                jti="privacy-session-active",
                expires_at=now + timedelta(hours=1),
            ),
            UserSession(
                user_id=member_user.id,
                jti="privacy-session-revoked",
                expires_at=now + timedelta(hours=1),
                revoked_at=now,
            ),
        ])
        db.commit()
        yield db
    engine.dispose()


def test_anonymize_user_preserves_existing_effects_with_bounded_tombstone(privacy_db):
    member_user = privacy_db.get(User, 1)
    assert member_user is not None
    original_email = member_user.email
    original_name = member_user.name
    original_phone = member_user.phone

    result = anonymize_user(privacy_db, user_id=member_user.id, admin_id=2)
    privacy_db.flush()

    assert result is member_user
    assert member_user.cpf == "ANON0000000001"
    assert len(member_user.cpf) <= 14
    assert ORIGINAL_MEMBER_CPF not in member_user.cpf
    assert original_email not in member_user.cpf
    assert original_name not in member_user.cpf
    assert original_phone not in member_user.cpf
    assert member_user.is_active is False
    assert member_user.phone is None
    assert member_user.name == "Usuário anonimizado"
    assert member_user.email != original_email

    sessions = privacy_db.scalars(
        select(UserSession).where(UserSession.user_id == member_user.id)
    ).all()
    assert len(sessions) == 2
    assert all(session.revoked_at is not None for session in sessions)

    member = privacy_db.scalar(select(Member).where(Member.user_id == member_user.id))
    assert member is not None
    assert member.status == "INACTIVE"

    access_log = privacy_db.scalar(
        select(DataAccessLog).where(DataAccessLog.action == "ANONYMIZE_USER")
    )
    assert access_log is not None
    assert access_log.actor_user_id == 2
    assert access_log.subject_user_id == member_user.id
    assert access_log.resource == "USER"
    log_values = " ".join(
        str(value)
        for value in (
            access_log.action,
            access_log.resource,
            access_log.ip_address,
            access_log.user_agent,
        )
        if value is not None
    )
    assert ORIGINAL_MEMBER_CPF not in log_values


def test_two_distinct_users_can_be_anonymized_without_unique_cpf_collision(privacy_db):
    first = anonymize_user(privacy_db, user_id=1, admin_id=2)
    second = anonymize_user(privacy_db, user_id=2, admin_id=1)
    privacy_db.flush()

    assert first.cpf == "ANON0000000001"
    assert second.cpf == "ANON0000000002"
    assert first.cpf != second.cpf
    assert len(first.cpf) <= 14
    assert len(second.cpf) <= 14
    assert privacy_db.scalar(
        select(sa.func.count()).select_from(DataAccessLog).where(
            DataAccessLog.action == "ANONYMIZE_USER"
        )
    ) == 2
