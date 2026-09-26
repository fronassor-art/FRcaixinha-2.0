"""Safety tests for the manually invoked legacy identity preflight runner."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import User
from app.ops import legacy_identity_preflight as runner
from app.services.legacy_identity_preflight import LegacyIdentityPreflightReport


CPF_A = "52998224725"
CPF_B = "11144477735"


def _user(user_id: int, *, cpf: str, email: str, active: bool = True) -> User:
    return User(
        id=user_id,
        name=f"Synthetic {user_id}",
        email=email,
        cpf=cpf,
        password_hash="test-only",
        is_active=active,
    )


def _prepare_database(path: Path, users: list[User] | None = None) -> None:
    engine = sa.create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    if users:
        with Session(engine) as db:
            db.add_all(users)
            db.commit()
    engine.dispose()


def _url_file(path: Path, database_path: Path, *, secret: str | None = None) -> Path:
    url_file = path / "database-url.txt"
    url = f"sqlite:///{database_path}"
    if secret:
        # The runner must not echo malformed URL contents or exception text.
        url = f"postgresql://audit:{secret}@invalid.example/never-connect"
    url_file.write_text(url + "\n", encoding="utf-8")
    return url_file


def _args(url_file: Path, *extra: str) -> list[str]:
    return [
        "--database-url-file",
        str(url_file),
        "--environment",
        "test",
        "--ack-read-only",
        "--confirm-no-real-users",
        *extra,
    ]


def test_import_does_not_create_engine_or_connect(tmp_path):
    code = (
        "import sqlalchemy; calls=[]; "
        "sqlalchemy.create_engine=lambda *a, **k: calls.append((a,k)); "
        "import app.ops.legacy_identity_preflight; "
        "assert calls == []"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "APP_ENV": "test", "DATABASE_URL": "sqlite:///unused.db"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "unused.db" not in result.stdout + result.stderr


def test_missing_readonly_ack_exits_before_engine_creation(tmp_path, monkeypatch):
    url_file = _url_file(tmp_path, tmp_path / "not-opened.db")
    monkeypatch.setattr(
        runner,
        "_create_readonly_engine",
        lambda *_args: pytest.fail("engine must not be created"),
    )

    from io import StringIO

    stderr = StringIO()
    result = runner.main(
        [
            "--database-url-file",
            str(url_file),
            "--environment",
            "test",
            "--confirm-no-real-users",
        ],
        stderr=stderr,
    )
    assert result == runner.EXIT_ARGUMENTS
    assert "Invalid arguments" in stderr.getvalue()


@pytest.mark.parametrize(
    ("configured_app_env", "environment"),
    [("production", "snapshot"), ("test", "production")],
)
def test_production_is_rejected_before_database_access(
    tmp_path, monkeypatch, configured_app_env, environment
):
    from io import StringIO

    monkeypatch.setenv("APP_ENV", configured_app_env)
    monkeypatch.setattr(
        runner,
        "_load_explicit_database_url",
        lambda *_args: pytest.fail("URL must not be read"),
    )
    stderr = StringIO()
    args = [
        "--database-url-file",
        str(tmp_path / "unused"),
        "--environment",
        environment,
        "--ack-read-only",
        "--confirm-snapshot-authorized",
        "--confirm-no-real-users",
    ]

    result = runner.main(args, stderr=stderr)
    assert result == runner.EXIT_ENVIRONMENT
    assert "environment" in stderr.getvalue().lower()


def test_loaded_settings_production_also_blocks(tmp_path, monkeypatch):
    from io import StringIO

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setitem(
        runner.sys.modules,
        "app.core.config",
        SimpleNamespace(settings=SimpleNamespace(app_env="production")),
    )
    monkeypatch.setattr(
        runner,
        "_load_explicit_database_url",
        lambda *_args: pytest.fail("URL must not be read"),
    )
    stderr = StringIO()
    result = runner.main(
        [
            "--database-url-file",
            str(tmp_path / "unused"),
            "--environment",
            "snapshot",
            "--ack-read-only",
            "--confirm-snapshot-authorized",
        ],
        stderr=stderr,
    )
    assert result == runner.EXIT_ENVIRONMENT
    assert "unsafe application environment" in stderr.getvalue().lower()


def test_unknown_environment_is_rejected(tmp_path, monkeypatch):
    from io import StringIO

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(
        runner,
        "_load_explicit_database_url",
        lambda *_args: pytest.fail("URL must not be read"),
    )
    stderr = StringIO()
    result = runner.main(
        [
            "--database-url-file",
            str(tmp_path / "unused"),
            "--environment",
            "qa-unknown",
            "--ack-read-only",
        ],
        stderr=stderr,
    )
    assert result == runner.EXIT_ENVIRONMENT


def test_snapshot_requires_explicit_authorization(tmp_path, monkeypatch):
    from io import StringIO

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(
        runner,
        "_load_explicit_database_url",
        lambda *_args: pytest.fail("URL must not be read"),
    )
    stderr = StringIO()
    result = runner.main(
        [
            "--database-url-file",
            str(tmp_path / "unused"),
            "--environment",
            "snapshot",
            "--ack-read-only",
        ],
        stderr=stderr,
    )
    assert result == runner.EXIT_ARGUMENTS
    assert "snapshot authorization" in stderr.getvalue().lower()


def test_database_url_and_credentials_never_appear_in_output(tmp_path, monkeypatch):
    from io import StringIO

    secret = "dont-print-this-password"
    url_file = _url_file(tmp_path, tmp_path / "unused.db", secret=secret)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(
        runner,
        "_run_preflight",
        lambda *_args: (_ for _ in ()).throw(runner._RunnerError(runner.EXIT_EXECUTION)),
    )
    stdout = StringIO()
    stderr = StringIO()
    result = runner.main(_args(url_file), stdout=stdout, stderr=stderr)

    assert result == runner.EXIT_EXECUTION
    assert secret not in stdout.getvalue() + stderr.getvalue()
    assert "postgresql://" not in stdout.getvalue() + stderr.getvalue()


def test_explicit_sqlite_database_runs_and_emits_only_aggregate_json(tmp_path, monkeypatch):
    from io import StringIO

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://ignored:secret@invalid.example/not-used")
    database_path = tmp_path / "empty.sqlite"
    _prepare_database(database_path)
    url_file = _url_file(tmp_path, database_path)
    stdout = StringIO()
    stderr = StringIO()

    result = runner.main(_args(url_file), stdout=stdout, stderr=stderr)

    payload = json.loads(stdout.getvalue())
    assert result == runner.EXIT_OK
    assert stderr.getvalue() == ""
    assert payload["environment"] == "test"
    assert payload["database_dialect"] == "sqlite"
    assert set(payload) == {"environment", "database_dialect", "report"}
    assert set(payload["report"]) == {
        field.name for field in fields(LegacyIdentityPreflightReport)
    }
    assert all(type(value) is int and value == 0 for value in payload["report"].values())
    assert str(database_path) not in stdout.getvalue()


def test_preflight_fixture_counts_are_aggregates_only(tmp_path, monkeypatch):
    from io import StringIO

    monkeypatch.setenv("APP_ENV", "test")
    database_path = tmp_path / "synthetic.sqlite"
    _prepare_database(
        database_path,
        [
            _user(1, cpf=CPF_A, email="legacy@example.com"),
            _user(2, cpf="529.982.247-25", email="LEGACY@example.com"),
        ],
    )
    url_file = _url_file(tmp_path, database_path)
    stdout = StringIO()
    result = runner.main(_args(url_file), stdout=stdout, stderr=StringIO())

    report = json.loads(stdout.getvalue())["report"]
    assert result == runner.EXIT_OK
    assert report["total_users"] == 2
    assert report["cpf_logical_collision_groups"] == 1
    assert report["cpf_logical_collision_users"] == 2
    assert report["email_case_collision_groups"] == 1
    assert report["email_case_collision_users"] == 2
    assert all(type(value) is int for value in report.values())
    assert "legacy@example.com" not in stdout.getvalue()
    assert "529.982.247-25" not in stdout.getvalue()


def test_runner_does_not_create_tables_or_run_migrations(tmp_path, monkeypatch):
    from io import StringIO

    monkeypatch.setenv("APP_ENV", "test")
    database_path = tmp_path / "blank.sqlite"
    sqlite3.connect(database_path).close()
    url_file = _url_file(tmp_path, database_path)
    stderr = StringIO()

    result = runner.main(_args(url_file), stdout=StringIO(), stderr=stderr)

    with sqlite3.connect(database_path) as db:
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    assert result == runner.EXIT_EXECUTION
    assert tables == []
    assert "alembic_version" not in {row[0] for row in tables}


def test_readonly_sql_guard_blocks_dml_and_ddl_and_allows_select():
    for statement in (
        "INSERT INTO users (id) VALUES (1)",
        "UPDATE users SET is_active = 0",
        "DELETE FROM users",
        "CREATE TABLE unsafe (id INTEGER)",
        "ALTER TABLE users ADD COLUMN unsafe INTEGER",
        "DROP TABLE users",
        "TRUNCATE users",
        "REPLACE INTO users (id) VALUES (1)",
    ):
        with pytest.raises(runner.ReadOnlyStatementError):
            runner._guard_readonly_statement(statement)

    runner._guard_readonly_statement("SELECT id FROM users")
    runner._guard_readonly_statement("-- report query\nSELECT id FROM users")


def test_sqlalchemy_engine_listener_blocks_dml_but_allows_select(tmp_path):
    database_path = tmp_path / "listener.sqlite"
    _prepare_database(database_path)
    engine = runner._create_readonly_engine(f"sqlite:///{database_path}", "sqlite")
    try:
        with engine.connect() as connection:
            assert connection.execute(select(User.id)).all() == []
            for statement in (
                "INSERT INTO users (id) VALUES (1)",
                "UPDATE users SET is_active = 0",
                "DELETE FROM users",
                "CREATE TABLE unsafe (id INTEGER)",
            ):
                with pytest.raises(runner.ReadOnlyStatementError):
                    connection.exec_driver_sql(statement)
    finally:
        engine.dispose()


def test_sqlite_readonly_mode_rejects_write_on_same_engine(tmp_path):
    database_path = tmp_path / "sqlite-actual-readonly.sqlite"
    _prepare_database(database_path)
    engine = runner._create_readonly_engine(f"sqlite:///{database_path}", "sqlite")
    try:
        raw_connection = engine.raw_connection()
        try:
            cursor = raw_connection.cursor()
            try:
                with pytest.raises(sqlite3.OperationalError, match="readonly"):
                    cursor.execute(
                        "INSERT INTO users "
                        "(name, email, cpf, password_hash, is_active) "
                        "VALUES ('x', 'x@example.com', 'bad', 'hash', 1)"
                    )
            finally:
                cursor.close()
        finally:
            raw_connection.close()
    finally:
        engine.dispose()


def test_session_is_closed_after_preflight(tmp_path, monkeypatch):
    database_path = tmp_path / "session.sqlite"
    _prepare_database(database_path)
    original_session = runner.Session
    tracked_sessions = []

    class TrackingSession(original_session):
        def close(self):
            tracked_sessions.append(self)
            super().close()

    monkeypatch.setattr(runner, "Session", TrackingSession)
    dialect, report = runner._run_preflight(f"sqlite:///{database_path}", "test")

    assert dialect == "sqlite"
    assert report.total_users == 0
    assert len(tracked_sessions) == 1
    assert not tracked_sessions[0].in_transaction()


def test_postgresql_readonly_policy_is_explicit_and_testable_without_server():
    assert runner._postgresql_readonly_sql() == "SET TRANSACTION READ ONLY"


def test_unsupported_dialect_is_rejected_without_engine_creation(monkeypatch):
    monkeypatch.setattr(
        runner,
        "create_engine",
        lambda *_args, **_kwargs: pytest.fail("unsupported dialect must not connect"),
    )
    with pytest.raises(runner._RunnerError) as error:
        runner._run_preflight("mysql://not-used/db", "test")
    assert error.value.exit_code == runner.EXIT_DIALECT
