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


def _snapshot(snapshot_date, snapshot_hash):
    return {
        "snapshot_date": snapshot_date,
        "status": "PASS",
        "snapshot_json": "{}",
        "snapshot_hash": snapshot_hash,
        "generated_by": None,
        "created_at": "2099-01-01 00:00:00",
    }


def test_migration_0054_sqlite_preserves_snapshot_constraints_and_indexes(tmp_path):
    database = tmp_path / "migration_0054.db"
    _alembic(database, "0054_executive_risk_response_v077")

    engine = create_engine(f"sqlite:///{database}")
    insert_sql = text(
        "INSERT INTO executive_risk_response_snapshots "
        "(snapshot_date, status, snapshot_json, snapshot_hash, generated_by, created_at) "
        "VALUES (:snapshot_date, :status, :snapshot_json, :snapshot_hash, "
        ":generated_by, :created_at)"
    )

    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0054_executive_risk_response_v077"
        )

        columns = {
            row[1]: row
            for row in connection.execute(
                text("PRAGMA table_info(executive_risk_response_snapshots)")
            )
        }
        assert all(
            name in columns
            for name in ("snapshot_date", "snapshot_hash", "generated_by")
        )
        assert columns["snapshot_date"][2] == "DATE"
        assert columns["snapshot_date"][3] == 1
        assert columns["snapshot_hash"][2] == "VARCHAR(64)"
        assert columns["snapshot_hash"][3] == 1
        assert columns["generated_by"][3] == 0

        ddl = connection.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'executive_risk_response_snapshots'"
            )
        ).scalar_one()
        assert "CONSTRAINT uq_executive_risk_response_snapshot_date UNIQUE (snapshot_date)" in ddl
        assert "CONSTRAINT uq_executive_risk_response_snapshot_hash UNIQUE (snapshot_hash)" in ddl

        unique_columns = []
        for row in connection.execute(
            text("PRAGMA index_list(executive_risk_response_snapshots)")
        ):
            if row[2]:
                unique_columns.append(
                    [
                        info[2]
                        for info in connection.execute(text(f'PRAGMA index_info("{row[1]}")'))
                    ]
                )
        assert ["snapshot_date"] in unique_columns
        assert ["snapshot_hash"] in unique_columns

        foreign_keys = [
            tuple(row)
            for row in connection.execute(
                text("PRAGMA foreign_key_list(executive_risk_response_snapshots)")
            )
        ]
        generated_by_fk = next(row for row in foreign_keys if row[3] == "generated_by")
        assert generated_by_fk[2] == "users"
        assert generated_by_fk[4] == "id"
        assert generated_by_fk[5] == "NO ACTION"
        assert generated_by_fk[6] == "NO ACTION"

        indexes = {
            row[1]: [
                info[2]
                for info in connection.execute(text(f'PRAGMA index_info("{row[1]}")'))
            ]
            for row in connection.execute(
                text("PRAGMA index_list(executive_risk_response_snapshots)")
            )
            if not row[2]
        }
        assert indexes["ix_exec_risk_response_status"] == ["status"]
        assert indexes["ix_exec_risk_response_created"] == ["created_at"]

        connection.execute(
            insert_sql,
            _snapshot("2099-01-01", "a" * 64),
        )

        try:
            connection.execute(
                insert_sql,
                _snapshot("2099-01-01", "b" * 64),
            )
        except IntegrityError as exc:
            assert "snapshot_date" in str(exc)
        else:
            raise AssertionError("duplicate snapshot_date was accepted")

        try:
            connection.execute(
                insert_sql,
                _snapshot("2099-01-02", "a" * 64),
            )
        except IntegrityError as exc:
            assert "snapshot_hash" in str(exc)
        else:
            raise AssertionError("duplicate snapshot_hash was accepted")

        connection.execute(
            insert_sql,
            _snapshot("2099-01-02", "b" * 64),
        )
