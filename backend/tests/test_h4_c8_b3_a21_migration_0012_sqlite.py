import os
import subprocess
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


def test_migration_0012_sqlite_preserves_snapshot_hash_uniqueness(tmp_path):
    database = tmp_path / "migration_0012.db"
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{database}",
            "JWT_SECRET": "testsecret",
            "APP_ENV": "test",
        }
    )

    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "upgrade",
            "0012_monthly_closing_integrity_v034",
        ],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO monthly_closings (competence, snapshot_hash, created_at) "
                "VALUES ('2099-01-01', 'duplicate-hash', '2099-01-01 00:00:00')"
            )
        )
        try:
            connection.execute(
                text(
                    "INSERT INTO monthly_closings (competence, snapshot_hash, created_at) "
                    "VALUES ('2099-02-01', 'duplicate-hash', '2099-02-01 00:00:00')"
                )
            )
        except IntegrityError:
            pass
        else:
            raise AssertionError("snapshot_hash uniqueness was not enforced")
