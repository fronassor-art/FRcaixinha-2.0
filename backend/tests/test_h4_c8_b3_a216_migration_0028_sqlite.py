import os
import subprocess
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


def _alembic(database, revision):
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{database}",
            "JWT_SECRET": "testsecret",
            "APP_ENV": "test",
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _snapshot_values(as_of_date, horizon_months, scenario, snapshot_hash):
    return {
        "as_of_date": as_of_date,
        "horizon_months": horizon_months,
        "scenario": scenario,
        "status": "PASS",
        "snapshot_json": "{}",
        "snapshot_hash": snapshot_hash,
        "created_at": "2099-01-01 00:00:00",
    }


def test_migration_0028_sqlite_preserves_projection_scope_uniqueness(tmp_path):
    database = tmp_path / "migration_0028.db"
    _alembic(database, "0028_financial_projection_v051")

    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0028_financial_projection_v051"
        )
        assert connection.execute(
            text(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'financial_projection_snapshots'"
            )
        ).scalar_one() == 1

        columns = connection.execute(text("PRAGMA table_info(financial_projection_snapshots)")).all()
        by_name = {row[1]: row for row in columns}
        assert [row[1] for row in columns] == [
            "id",
            "as_of_date",
            "horizon_months",
            "scenario",
            "status",
            "snapshot_json",
            "snapshot_hash",
            "generated_by",
            "created_at",
        ]
        assert by_name["as_of_date"][2] == "DATE"
        assert by_name["horizon_months"][2] == "INTEGER"
        assert by_name["scenario"][2] == "VARCHAR(20)"
        assert all(by_name[name][3] == 1 for name in ("as_of_date", "horizon_months", "scenario"))

        unique_scopes = []
        for row in connection.execute(text("PRAGMA index_list(financial_projection_snapshots)")):
            if row[2]:
                index_columns = [
                    info[2]
                    for info in connection.execute(text(f'PRAGMA index_info("{row[1]}")'))
                ]
                unique_scopes.append(index_columns)
        assert ["as_of_date", "horizon_months", "scenario"] in unique_scopes

    insert_sql = text(
        "INSERT INTO financial_projection_snapshots "
        "(as_of_date, horizon_months, scenario, status, snapshot_json, snapshot_hash, created_at) "
        "VALUES (:as_of_date, :horizon_months, :scenario, :status, :snapshot_json, :snapshot_hash, :created_at)"
    )
    with engine.begin() as connection:
        connection.execute(
            insert_sql,
            _snapshot_values("2099-01-01", 12, "BASE", "scope-hash-1"),
        )

    with engine.begin() as connection:
        try:
            connection.execute(
                insert_sql,
                _snapshot_values("2099-01-01", 12, "BASE", "scope-hash-2"),
            )
        except IntegrityError:
            pass
        else:
            raise AssertionError("duplicate projection scope was not rejected")

    with engine.begin() as connection:
        connection.execute(
            insert_sql,
            _snapshot_values("2099-01-01", 24, "BASE", "scope-hash-3"),
        )
