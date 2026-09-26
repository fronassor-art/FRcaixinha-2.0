"""Manually run the legacy identity preflight against an explicit read-only DB."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence, TextIO
from urllib.parse import quote

from sqlalchemy import URL, Engine, create_engine, event, make_url
from sqlalchemy.orm import Session

from app.services.legacy_identity_preflight import (
    LegacyIdentityPreflightReport,
    evaluate_legacy_identity_preflight,
)


EXIT_OK = 0
EXIT_ARGUMENTS = 2
EXIT_ENVIRONMENT = 3
EXIT_DIALECT = 4
EXIT_READONLY = 5
EXIT_EXECUTION = 6

_ALLOWED_ENVIRONMENTS = {"test", "development", "snapshot"}
_READ_ONLY_SQL = re.compile(
    r"^(?:INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|REPLACE|"
    r"ATTACH|DETACH|VACUUM|REINDEX|ANALYZE|GRANT|REVOKE|PRAGMA)\b",
    re.IGNORECASE,
)


class _ArgumentError(Exception):
    pass


class _RunnerError(Exception):
    def __init__(self, exit_code: int):
        self.exit_code = exit_code


class ReadOnlyStatementError(RuntimeError):
    """Raised without embedding the rejected SQL or its parameters."""


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise _ArgumentError


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        description="Run an aggregate-only legacy identity preflight.",
        allow_abbrev=False,
    )
    parser.add_argument("--database-url-file")
    parser.add_argument("--environment")
    parser.add_argument("--ack-read-only", action="store_true")
    parser.add_argument("--confirm-no-real-users", action="store_true")
    parser.add_argument("--confirm-snapshot-authorized", action="store_true")
    return parser


def _configured_app_environments() -> tuple[str, ...]:
    """Read only the effective app environment; never load DB settings/engines."""
    configured: list[str] = []
    environment_value = os.environ.get("APP_ENV")
    if environment_value is not None:
        configured.append(environment_value.strip().lower())

    loaded_config = sys.modules.get("app.core.config")
    loaded_settings = getattr(loaded_config, "settings", None)
    loaded_value = getattr(loaded_settings, "app_env", None)
    if loaded_value is not None:
        configured.append(str(loaded_value).strip().lower())
    elif environment_value is None:
        try:
            dotenv_value = _read_app_env_from_dotenv(Path(".env"))
        except Exception:
            # Failure to inspect an explicitly configured environment must
            # fail closed in the caller.
            configured.append("__unreadable__")
        else:
            if dotenv_value is not None:
                configured.append(str(dotenv_value).strip().lower())
    return tuple(configured)


def _read_app_env_from_dotenv(path: Path) -> str | None:
    """Read only APP_ENV from the same cwd-relative dotenv used by Settings."""
    if not path.exists():
        return None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, value = line.partition("=")
        if separator and key.strip().upper() == "APP_ENV":
            result = value.strip()
            if len(result) >= 2 and result[0] == result[-1] and result[0] in "\"'":
                result = result[1:-1]
            return result
    return None


def _unsafe_app_environment_is_configured() -> bool:
    return any(
        value not in _ALLOWED_ENVIRONMENTS or value == "__unreadable__"
        for value in _configured_app_environments()
    )


def _postgresql_readonly_sql() -> str:
    """Return the transaction-level PostgreSQL read-only command."""
    return "SET TRANSACTION READ ONLY"


def _sqlite_readonly_url(explicit_url: str) -> URL:
    """Build a SQLite URI that opens an existing file in mode=ro."""
    try:
        parsed = make_url(explicit_url)
    except Exception:
        raise _RunnerError(EXIT_EXECUTION) from None

    if parsed.get_backend_name() != "sqlite":
        raise _RunnerError(EXIT_DIALECT)
    if parsed.query or not parsed.database or parsed.database == ":memory:":
        raise _RunnerError(EXIT_READONLY)
    if parsed.database.startswith("file:"):
        raise _RunnerError(EXIT_READONLY)

    try:
        database_path = Path(parsed.database).expanduser().resolve(strict=True)
        if not database_path.is_file():
            raise _RunnerError(EXIT_READONLY)
    except _RunnerError:
        raise
    except Exception:
        raise _RunnerError(EXIT_READONLY) from None

    file_uri = "file:" + quote(str(database_path), safe="/")
    return URL.create(
        drivername="sqlite+pysqlite",
        database=file_uri,
        query={"mode": "ro", "uri": "true"},
    )


def _guard_readonly_statement(statement: str) -> None:
    candidate = statement.lstrip()
    while candidate.startswith("--") or candidate.startswith("/*"):
        if candidate.startswith("--"):
            newline = candidate.find("\n")
            candidate = "" if newline < 0 else candidate[newline + 1 :].lstrip()
            continue
        end_comment = candidate.find("*/", 2)
        if end_comment < 0:
            raise ReadOnlyStatementError("read-only SQL guard rejected statement")
        candidate = candidate[end_comment + 2 :].lstrip()

    if _READ_ONLY_SQL.match(candidate):
        raise ReadOnlyStatementError("read-only SQL guard rejected statement")


def _create_readonly_engine(explicit_url: str, dialect: str) -> Engine:
    if dialect == "sqlite":
        engine_url = _sqlite_readonly_url(explicit_url)
    elif dialect == "postgresql":
        try:
            engine_url = make_url(explicit_url)
        except Exception:
            raise _RunnerError(EXIT_EXECUTION) from None
    else:
        raise _RunnerError(EXIT_DIALECT)

    try:
        engine = create_engine(engine_url, pool_pre_ping=True)
    except Exception:
        raise _RunnerError(EXIT_EXECUTION) from None

    if dialect == "sqlite":
        def enable_sqlite_readonly(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                # Connection-local SQLite protection complements the file URI
                # mode=ro and is set before any ORM statement can run.
                cursor.execute("PRAGMA query_only=ON")
                cursor.execute("PRAGMA query_only")
                row = cursor.fetchone()
                if row is None or row[0] != 1:
                    raise RuntimeError("SQLite read-only mode unavailable")
            finally:
                cursor.close()

        event.listen(engine, "connect", enable_sqlite_readonly)

    def block_write_sql(_connection, _cursor, statement, _parameters, _context, _many):
        _guard_readonly_statement(statement)

    event.listen(engine, "before_cursor_execute", block_write_sql)
    return engine


def _verify_sqlite_readonly(engine: Engine) -> None:
    try:
        raw_connection = engine.raw_connection()
        try:
            cursor = raw_connection.cursor()
            try:
                cursor.execute("PRAGMA query_only")
                row = cursor.fetchone()
                if row is None or row[0] != 1:
                    raise RuntimeError("SQLite read-only mode unavailable")
            finally:
                cursor.close()
        finally:
            raw_connection.close()
    except Exception:
        raise _RunnerError(EXIT_READONLY) from None


def _establish_postgresql_readonly(session: Session) -> None:
    session.connection().exec_driver_sql(_postgresql_readonly_sql())


def _load_explicit_database_url(path_value: str) -> str:
    try:
        contents = Path(path_value).expanduser().read_text(encoding="utf-8")
    except Exception:
        raise _RunnerError(EXIT_EXECUTION) from None

    explicit_url = contents.strip()
    if not explicit_url or len(contents.splitlines()) > 1:
        raise _RunnerError(EXIT_ARGUMENTS)
    return explicit_url


def _run_preflight(
    explicit_url: str, environment: str
) -> tuple[str, LegacyIdentityPreflightReport]:
    try:
        parsed_url = make_url(explicit_url)
    except Exception:
        raise _RunnerError(EXIT_EXECUTION) from None
    dialect = parsed_url.get_backend_name()
    if dialect not in {"sqlite", "postgresql"}:
        raise _RunnerError(EXIT_DIALECT)

    engine = _create_readonly_engine(explicit_url, dialect)
    try:
        if dialect == "sqlite":
            _verify_sqlite_readonly(engine)

        try:
            with Session(engine) as session:
                if dialect == "postgresql":
                    try:
                        session.connection()
                    except Exception:
                        raise _RunnerError(EXIT_EXECUTION) from None
                    try:
                        _establish_postgresql_readonly(session)
                    except Exception:
                        raise _RunnerError(EXIT_READONLY) from None

                try:
                    report = evaluate_legacy_identity_preflight(session)
                except Exception:
                    raise _RunnerError(EXIT_EXECUTION) from None
        except _RunnerError:
            raise
        except Exception:
            raise _RunnerError(EXIT_EXECUTION) from None
    finally:
        engine.dispose()

    if any(type(value) is not int for value in asdict(report).values()):
        raise _RunnerError(EXIT_EXECUTION)
    return dialect, report


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except _ArgumentError:
        print("Invalid arguments.", file=errors)
        return EXIT_ARGUMENTS
    except SystemExit as exc:
        return int(exc.code or 0)

    if not args.database_url_file or not args.environment or not args.ack_read_only:
        print("Invalid arguments.", file=errors)
        return EXIT_ARGUMENTS

    environment = args.environment.strip().lower()
    if environment not in _ALLOWED_ENVIRONMENTS:
        print("Environment is not allowed.", file=errors)
        return EXIT_ENVIRONMENT
    if _unsafe_app_environment_is_configured():
        print("Unsafe application environment is not allowed.", file=errors)
        return EXIT_ENVIRONMENT
    if environment in {"test", "development"} and not args.confirm_no_real_users:
        print("Explicit no-real-users confirmation is required.", file=errors)
        return EXIT_ARGUMENTS
    if environment == "snapshot" and not args.confirm_snapshot_authorized:
        print("Explicit snapshot authorization is required.", file=errors)
        return EXIT_ARGUMENTS

    try:
        explicit_url = _load_explicit_database_url(args.database_url_file)
        dialect, report = _run_preflight(explicit_url, environment)
    except _RunnerError as exc:
        messages = {
            EXIT_ARGUMENTS: "Invalid arguments.",
            EXIT_ENVIRONMENT: "Environment is not allowed.",
            EXIT_DIALECT: "Database dialect is not supported.",
            EXIT_READONLY: "Read-only protection could not be established.",
            EXIT_EXECUTION: "Preflight execution failed.",
        }
        print(messages[exc.exit_code], file=errors)
        return exc.exit_code
    except Exception:
        # Do not expose exception text: DB drivers may include connection URLs.
        print("Preflight execution failed.", file=errors)
        return EXIT_EXECUTION

    payload = {
        "environment": environment,
        "database_dialect": dialect,
        "report": asdict(report),
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), file=output)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
