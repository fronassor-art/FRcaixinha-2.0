import importlib.util
from pathlib import Path
from sqlalchemy import CheckConstraint, create_engine, inspect, text
from app.db.base import Base
from app.models import Contribution, Cycle, Quota

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "m0096", ROOT / "alembic/versions/0096_cycle_foundation_a377a.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_revision_is_direct_child_of_0095_and_first_cycle_is_explicit():
    assert module.revision == "0096_cycle_foundation_a377a"
    assert module.down_revision == "0095_pix_attempt_provider_reservation"
    source = Path(module.__file__).read_text()
    assert "2026-12-10" in source
    assert "2027-01-10" in source
    assert "2027-12-10" in source
    assert "UPDATE " not in source.upper()
    assert "DELETE FROM" not in source.upper()


def test_models_scope_cycle_and_preserve_nullable_legacy_rows():
    assert Quota.__table__.c.cycle_id.nullable
    assert Contribution.__table__.c.cycle_id.nullable
    assert Cycle.__table__.c.max_quotas is not None
    assert any(isinstance(c, CheckConstraint) for c in Cycle.__table__.constraints)


def test_sqlite_metadata_has_cycle_scoped_partial_identities():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    indexes = {item["name"]: item for item in inspect(engine).get_indexes("contributions")}
    assert indexes["uq_contribution_member_cycle_competence"]["unique"] == 1
    assert indexes["uq_contribution_legacy_member_competence"]["unique"] == 1
