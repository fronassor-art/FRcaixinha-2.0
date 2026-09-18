import os
import subprocess
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


def test_migration_0013_sqlite_preserves_entry_hash_uniqueness(tmp_path):
    database = tmp_path / "migration_0013.db"
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
            "0013_ledger_hardening_v035",
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
                "INSERT INTO ledger_entries "
                "(account, direction, amount, reference_type, reference_id, "
                "created_at, previous_hash, entry_hash) "
                "VALUES ('CAIXINHA', 'CREDIT', 10.00, 'TEST', '1', "
                "'2099-01-01 00:00:00', NULL, 'duplicate-hash')"
            )
        )
        try:
            connection.execute(
                text(
                    "INSERT INTO ledger_entries "
                    "(account, direction, amount, reference_type, reference_id, "
                    "created_at, previous_hash, entry_hash) "
                    "VALUES ('CAIXINHA', 'CREDIT', 20.00, 'TEST', '2', "
                    "'2099-01-02 00:00:00', NULL, 'duplicate-hash')"
                )
            )
        except IntegrityError:
            pass
        else:
            raise AssertionError("entry_hash uniqueness was not enforced")
