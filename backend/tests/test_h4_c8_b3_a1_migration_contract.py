from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_collection_subjects_migration_remains_in_revision_chain():
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    script = ScriptDirectory.from_config(config)

    assert script.get_current_head() == "0097_cycle_participation_a377b1"
    assert script.get_revision("0097_cycle_participation_a377b1").down_revision == "0096_cycle_foundation_a377a"
    revision = script.get_revision("0089_collection_agreement_subjects")
    assert revision.down_revision == "0088_monthly_closing_snapshot_schema_h3c_b2"
