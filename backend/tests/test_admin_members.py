from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import require_admin
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import Group, Member, Quota, User


@pytest.fixture()
def admin_members_client():
    engine = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    def test_db():
        with factory() as db:
            yield db

    previous_db = app.dependency_overrides.get(get_db)
    previous_admin = app.dependency_overrides.get(require_admin)
    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(id=1, role="ADMIN")
    try:
        with TestClient(app) as client:
            yield client, factory
    finally:
        if previous_db is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous_db
        if previous_admin is None:
            app.dependency_overrides.pop(require_admin, None)
        else:
            app.dependency_overrides[require_admin] = previous_admin
        engine.dispose()


def add_member(factory, *, member_id=1, units=Decimal("2.5000")):
    with factory() as db:
        db.add(Group(id=1, name="Test Group"))
        db.add(User(
            id=member_id,
            name="Test Member",
            email=f"member{member_id}@example.test",
            cpf=f"00000000000{member_id:03d}",
            password_hash="test-only",
            role="USER",
        ))
        db.flush()
        db.add(Member(id=member_id, user_id=member_id, group_id=1))
        db.flush()
        db.add(Quota(member_id=member_id, units=units))
        db.commit()


def test_admin_members_empty_list_returns_empty_items(admin_members_client):
    client, _factory = admin_members_client

    response = client.get("/api/admin/members")

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_admin_members_returns_member_and_quota_units(admin_members_client):
    client, factory = admin_members_client
    add_member(factory, units=Decimal("2.5000"))

    response = client.get("/api/admin/members")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    member = body["items"][0]
    assert member["id"] == 1
    assert member["status"] == "ACTIVE"
    assert member["joined_at"]
    assert member["user"] == {
        "id": 1,
        "name": "Test Member",
        "email": "member1@example.test",
        "cpf": "00000000000001",
        "phone": None,
        "is_active": True,
    }
    assert member["group_id"] == 1
    assert member["quota_units"] == "2.50"


def test_admin_member_detail_loads_member_relationships(admin_members_client):
    client, factory = admin_members_client
    add_member(factory, units=Decimal("3.0000"))

    response = client.get("/api/admin/members/1")

    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "status": "ACTIVE",
        "group_id": 1,
        "user": {
            "id": 1,
            "name": "Test Member",
            "email": "member1@example.test",
            "cpf": "00000000000001",
            "phone": None,
            "is_active": True,
        },
        "quota_units": "3.00",
        "contributions": [],
        "loans": [],
    }
