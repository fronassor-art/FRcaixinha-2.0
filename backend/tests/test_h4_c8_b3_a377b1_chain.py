"""Run the real Alembic chain without application secret settings."""
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).parents[1]


def chain(url):
    env = os.environ.copy()
    env["A377_CHAIN_URL"] = url
    script = r"""
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

ROOT = Path.cwd()
url = os.environ["A377_CHAIN_URL"]

config = Config(str(ROOT / "alembic.ini"))
config.set_main_option("script_location", str(ROOT / "alembic"))

fake = ModuleType("app.core.config")
fake.settings = SimpleNamespace(database_url=url)

with patch.dict(sys.modules, {"app.core.config": fake}):
    command.upgrade(config, "head")

    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            revision = connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert revision == "0099_cycle_closing_review_a377b3r1"
    finally:
        engine.dispose()

    command.downgrade(config, "-1")
    command.upgrade(config, "head")
"""
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        check=True,
    )


def test_sqlite_full_chain_upgrade_downgrade_upgrade(tmp_path):
    chain(f"sqlite:///{tmp_path / 'a377b1_chain.db'}")


def test_postgresql_full_chain_upgrade_downgrade_upgrade_if_configured():
    url = os.environ.get("A377B1_POSTGRES_TEST_URL")
    if not url:
        pytest.skip("A377B1_POSTGRES_TEST_URL is not configured")
    parsed = sa.engine.make_url(url)
    if parsed.get_backend_name() != "postgresql" or "a377b1_test" not in (parsed.database or "").lower():
        pytest.fail("PostgreSQL URL must target a dedicated a377b1_test database")
    chain(url)
