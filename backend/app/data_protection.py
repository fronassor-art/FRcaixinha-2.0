"""Local, encrypted PostgreSQL/evidence backup and isolated restore core.

This module deliberately has no Google API client or production restore path.
The local COMPLETE_LOCAL state is not proof of an off-VM copy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Protocol, Sequence


EXPECTED_HEAD = "0106_event_financial_date_f2e1"
MANIFEST_VERSION = 1
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_RESTORE_DB = re.compile(r"frcaixinha_restore_[a-z0-9_]+\Z")


class BackupError(RuntimeError):
    """A safe error code; never include command output or credentials."""


class OffVmDestination(Protocol):
    """Phase B adapter contract. No provider implementation exists in Phase A."""

    def upload_resumable(self, backup_id: str, local_path: Path) -> str: ...
    def describe(self, file_id: str) -> dict: ...
    def download(self, file_id: str, target: Path) -> None: ...
    def list_backups(self, folder_id: str) -> list[dict]: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _metadata(path: Path) -> dict:
    return {"filename": path.name, "size": path.stat().st_size, "sha256": _sha256(path)}


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("x", encoding="utf-8") as out:
        os.chmod(temporary, 0o600)
        json.dump(payload, out, sort_keys=True, separators=(",", ":"))
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(path)


def _pg_env() -> dict[str, str]:
    required = ("PGHOST", "PGPORT", "PGUSER", "PGDATABASE", "PGPASSFILE")
    if any(not os.environ.get(name) for name in required) or os.environ.get("PGPASSWORD"):
        raise BackupError("pg_connection_config_invalid")
    passfile = Path(os.environ["PGPASSFILE"])
    if not passfile.is_file() or passfile.stat().st_mode & 0o077:
        raise BackupError("pg_passfile_permissions_invalid")
    return {**{name: os.environ[name] for name in required}, "PATH": os.environ.get("PATH", "")}


def _pg_connect(env: dict[str, str]):
    import psycopg

    return psycopg.connect(
        host=env["PGHOST"], port=env["PGPORT"], user=env["PGUSER"],
        dbname=env["PGDATABASE"], passfile=env["PGPASSFILE"], autocommit=True,
    )


def _run(arguments: list[str], *, env: dict[str, str], stdin=None, stdout=None) -> None:
    result = subprocess.run(arguments, env=env, stdin=stdin, stdout=stdout,
                            stderr=subprocess.DEVNULL, check=False)
    if result.returncode:
        raise BackupError("tool_failed")


def _evidence_refs(conn) -> list[dict]:
    refs = []
    execute = conn.exec_driver_sql if hasattr(conn, "exec_driver_sql") else conn.execute
    for table, prefix in (
        ("workflow_execution_evidence_files", ""),
        ("continuous_improvement_execution_evidence_files", "continuous-improvement/"),
    ):
        # Identifiers are constants, never user input.
        for row_id, key, size, digest in execute(
            f"SELECT id, storage_key, size_bytes, sha256 FROM {table} ORDER BY id"
        ):
            if not key or not _HASH.fullmatch(digest or "") or size < 0:
                raise BackupError("evidence_metadata_invalid")
            relative = PurePosixPath(prefix + key)
            if relative.is_absolute() or ".." in relative.parts or any(
                part in {"", "."} for part in relative.parts
            ):
                raise BackupError("evidence_key_invalid")
            refs.append({"table": table, "row_id": row_id, "path": str(relative),
                         "size": size, "sha256": digest})
    return refs


def _open_evidence(root: Path, relative: str):
    root = root.resolve(strict=True)
    path = root / relative
    if not path.exists():
        raise BackupError("evidence_missing")
    if not path.resolve(strict=True).is_relative_to(root):
        raise BackupError("evidence_path_invalid")
    current = root
    for component in PurePosixPath(relative).parts:
        current /= component
        if stat.S_ISLNK(current.lstat().st_mode):
            raise BackupError("evidence_symlink_rejected")
    source = path.open("rb")
    if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
        source.close()
        raise BackupError("evidence_not_regular")
    return source


def _recipients(value: str | Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, str):
        parts = value.replace("\n", ",").split(",")
    else:
        parts = list(value)
    if not parts or any(not item.strip() for item in parts):
        raise BackupError("age_recipient_invalid")
    recipients = tuple(item.strip() for item in parts)
    if not recipients or any(not recipient.startswith("age1") for recipient in recipients):
        raise BackupError("age_recipient_invalid")
    if len(set(recipients)) != len(recipients):
        raise BackupError("age_recipient_duplicate")
    return recipients


def _encrypt_stream(source, output: Path, recipient: str | Sequence[str]) -> tuple[int, str]:
    recipients = _recipients(recipient)
    command = ["age"]
    for item in recipients:
        command.extend(("-r", item))
    command.extend(("-o", str(output)))
    process = subprocess.Popen(command,
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    digest = hashlib.sha256()
    size = 0
    try:
        assert process.stdin is not None
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
            process.stdin.write(block)
        process.stdin.close()
        if process.wait() != 0:
            raise BackupError("age_encrypt_failed")
    except Exception:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        process.wait()
        output.unlink(missing_ok=True)
        raise
    return size, digest.hexdigest()


def _dump_encrypted(env: dict[str, str], snapshot: str, output: Path,
                    recipient: str | Sequence[str]) -> None:
    command = ["pg_dump", "--format=custom", "--snapshot", snapshot,
               "--dbname", env["PGDATABASE"]]
    dump = subprocess.Popen(command, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    assert dump.stdout is not None
    try:
        _encrypt_stream(dump.stdout, output, recipient)
        if dump.wait() != 0:
            raise BackupError("pg_dump_failed")
    except Exception:
        dump.stdout.close()
        dump.wait()
        output.unlink(missing_ok=True)
        raise


def _config() -> tuple[str, str, tuple[str, ...]]:
    provider = os.environ.get("BACKUP_OFF_VM_PROVIDER")
    folder = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
    single_recipient = os.environ.get("BACKUP_AGE_RECIPIENT")
    multiple_recipients = os.environ.get("BACKUP_AGE_RECIPIENTS")
    if provider != "google_drive" or not folder or not re.fullmatch(r"[A-Za-z0-9_-]+", folder):
        raise BackupError("destination_config_invalid")
    if single_recipient and multiple_recipients:
        raise BackupError("age_recipient_config_ambiguous")
    recipient_value = multiple_recipients or single_recipient or ""
    if not recipient_value.strip():
        raise BackupError("age_recipient_missing")
    try:
        recipients = _recipients(recipient_value)
    except BackupError:
        raise
    return provider, folder, recipients


def create_backup(staging_root: Path, evidence_root: Path) -> Path:
    """Produce an encrypted, locally complete set; never upload it."""
    env = _pg_env()
    provider, folder, recipients = _config()
    commit = os.environ.get("BACKUP_APPLICATION_COMMIT", "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise BackupError("application_commit_missing")
    if (not staging_root.is_dir() or staging_root.is_symlink()
            or staging_root.stat().st_mode & 0o077):
        raise BackupError("staging_root_invalid")
    backup_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex
    directory = staging_root / backup_id
    directory.mkdir(mode=0o700)
    manifest = {"manifest_version": MANIFEST_VERSION, "backup_id": backup_id,
                "started_at_utc": _now(), "completed_at_utc": None,
                "application_commit": commit, "alembic_head": None,
                "postgres_version": None, "pg_dump_version": None,
                "archive_format": "custom", "archive": None, "encryption": "age",
                "encryption_recipient_identifier": hashlib.sha256(
                    "\n".join(sorted(recipients)).encode()
                ).hexdigest()[:16],
                "evidence_file_count": 0, "evidence": [], "off_vm_provider": provider,
                "off_vm_folder_id": folder, "remote_file_id": None,
                "off_vm_status": "NOT_UPLOADED", "status": "IN_PROGRESS"}
    _write_json(directory / "manifest.json", manifest)
    try:
        with _pg_connect(env) as conn:
            conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            snapshot = conn.execute("SELECT pg_export_snapshot()").fetchone()[0]
            head = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            if head != EXPECTED_HEAD:
                raise BackupError("alembic_head_unexpected")
            manifest["alembic_head"] = head
            manifest["postgres_version"] = conn.execute("SHOW server_version").fetchone()[0]
            if int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000 != 16:
                raise BackupError("postgres_version_unexpected")
            refs = _evidence_refs(conn)
            result = subprocess.run(["pg_dump", "--version"], env=env,
                                    capture_output=True, text=True, check=False)
            if result.returncode or not result.stdout.startswith("pg_dump (PostgreSQL) 16."):
                raise BackupError("pg_dump_version_unexpected")
            manifest["pg_dump_version"] = result.stdout.strip()
            archive = directory / "postgres.dump.age"
            _dump_encrypted(env, snapshot, archive, recipients)
            conn.execute("ROLLBACK")
        manifest["archive"] = _metadata(archive)
        evidence_dir = directory / "evidence"
        evidence_dir.mkdir(mode=0o700)
        for ref in refs:
            target = evidence_dir / f"{ref['table']}-{ref['row_id']}.age"
            with _open_evidence(evidence_root, ref["path"]) as source:
                size, digest = _encrypt_stream(source, target, recipients)
            if size != ref["size"] or digest != ref["sha256"]:
                target.unlink(missing_ok=True)
                raise BackupError("evidence_content_mismatch")
            manifest["evidence"].append({**ref, "encrypted": _metadata(target)})
        manifest["evidence_file_count"] = len(refs)
        manifest["completed_at_utc"] = _now()
        manifest["status"] = "COMPLETE_LOCAL"
        _write_json(directory / "manifest.json", manifest)
        return directory
    except Exception as exc:
        manifest["status"] = "FAILED"
        manifest["error_code"] = exc.args[0] if isinstance(exc, BackupError) else "backup_failed"
        _write_json(directory / "manifest.json", manifest)
        raise BackupError(manifest["error_code"]) from None


def _verify_file(root: Path, metadata: dict) -> Path:
    name = metadata.get("filename")
    if not isinstance(name, str) or Path(name).name != name:
        raise BackupError("manifest_filename_invalid")
    path = root / name
    if path.is_symlink() or not path.is_file() or path.stat().st_size != metadata.get("size") or _sha256(path) != metadata.get("sha256"):
        raise BackupError("archive_checksum_mismatch")
    return path


def _decrypt(source: Path, target: Path, identity: Path) -> None:
    _run(["age", "--decrypt", "--identity", str(identity), "--output", str(target), str(source)],
         env=os.environ.copy())


def _assert_restore_target(env: dict[str, str]) -> None:
    if os.environ.get("FRCAIXINHA_ISOLATED_RESTORE") != "YES" or not _RESTORE_DB.fullmatch(env["PGDATABASE"]):
        raise BackupError("restore_target_not_isolated")
    if os.environ.get("APP_ENV") != "test":
        raise BackupError("restore_test_environment_required")
    external_names = (
        "MERCADO_PAGO_ACCESS_TOKEN", "MERCADO_PAGO_WEBHOOK_SECRET",
        "SMTP_PASSWORD", "WHATSAPP_TOKEN", "PUSH_CREDENTIALS",
    )
    if any(os.environ.get(name) for name in external_names):
        raise BackupError("external_credentials_present")
    if env["PGHOST"] not in {"127.0.0.1", "localhost", "::1"}:
        raise BackupError("restore_host_not_loopback")
    with _pg_connect(env) as conn:
        if int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000 != 16:
            raise BackupError("postgres_version_unexpected")
        count = conn.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                             "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m')").fetchone()[0]
        if count:
            raise BackupError("restore_database_not_empty")


def _read_only_validation(env: dict[str, str], evidence_root: Path, manifest: dict) -> dict:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import URL
    from sqlalchemy.orm import Session
    from app.services.ledger import verify_ledger_chain
    from app.services.reconciliation_v032 import reconcile
    from app.services.workflow_evidence_integrity_v069 import verify_chain as workflow_chain
    from app.services.continuous_improvement_evidence_v089 import verify_chain as improvement_chain

    url = URL.create("postgresql+psycopg", username=env["PGUSER"], host=env["PGHOST"],
                     port=int(env["PGPORT"]), database=env["PGDATABASE"])
    engine = create_engine(url, connect_args={"passfile": env["PGPASSFILE"]})
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            with Session(bind=connection, autoflush=False) as db:
                head = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                if head != EXPECTED_HEAD or head != manifest["alembic_head"]:
                    raise BackupError("restored_alembic_head_mismatch")
                if connection.execute(text("SELECT pg_get_serial_sequence('ledger_entries','id')")).scalar_one() is None:
                    raise BackupError("ledger_sequence_missing")
                guard = connection.execute(text("SELECT count(*) FROM pg_trigger WHERE tgname='trg_ledger_append_only' AND NOT tgisinternal")).scalar_one()
                if guard != 1:
                    raise BackupError("ledger_trigger_missing")
                checks = {"ledger": verify_ledger_chain(db), "reconciliation": reconcile(db),
                          "workflow_chain": workflow_chain(db), "improvement_chain": improvement_chain(db)}
                if checks["ledger"]["status"] != "PASS" or not checks["workflow_chain"]["valid"] or not checks["improvement_chain"]["valid"]:
                    raise BackupError("financial_integrity_check_failed")
                # Reconciliation findings are reported, not silently rewritten or treated as restore damage.
                rows = _evidence_refs(connection)
                expected = [{key: ref[key] for key in ("table", "row_id", "path", "size", "sha256")}
                            for ref in manifest["evidence"]]
                if rows != expected:
                    raise BackupError("restored_evidence_inventory_mismatch")
                for ref in rows:
                    with _open_evidence(evidence_root, ref["path"]) as source:
                        digest = hashlib.sha256()
                        size = 0
                        for block in iter(lambda: source.read(1024 * 1024), b""):
                            size += len(block)
                            digest.update(block)
                    if digest.hexdigest() != ref["sha256"] or size != ref["size"]:
                        raise BackupError("restored_evidence_mismatch")
                return {"alembic_head": head, "ledger": checks["ledger"]["status"],
                        "reconciliation": checks["reconciliation"]["status"],
                        "evidence_files": len(rows)}
    finally:
        engine.dispose()


def restore_isolated(directory: Path, evidence_root: Path, identity: Path) -> dict:
    """Restore only to a fresh loopback PostgreSQL database with an explicit test marker."""
    env = _pg_env()
    _assert_restore_target(env)
    if (not identity.is_file() or identity.stat().st_mode & 0o077
            or evidence_root.is_symlink() or not evidence_root.is_dir()
            or evidence_root.stat().st_mode & 0o077 or any(evidence_root.iterdir())):
        raise BackupError("restore_inputs_invalid")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("manifest_version") != MANIFEST_VERSION or manifest.get("status") != "COMPLETE_LOCAL":
        raise BackupError("backup_not_complete_local")
    if manifest.get("off_vm_provider") != "google_drive" or manifest.get("alembic_head") != EXPECTED_HEAD:
        raise BackupError("manifest_contract_invalid")
    archive = _verify_file(directory, manifest["archive"])
    evidence_files = []
    for ref in manifest["evidence"]:
        evidence_files.append((_verify_file(directory / "evidence", ref["encrypted"]), ref))
    if len(evidence_files) != manifest["evidence_file_count"]:
        raise BackupError("evidence_count_mismatch")
    with tempfile.TemporaryDirectory(prefix="frcaixinha-restore-") as temporary:
        clear_archive = Path(temporary) / "postgres.dump"
        _decrypt(archive, clear_archive, identity)
        _run(["pg_restore", "--list", str(clear_archive)], env=env, stdout=subprocess.DEVNULL)
        for encrypted, ref in evidence_files:
            relative = PurePosixPath(ref["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise BackupError("evidence_key_invalid")
            target = evidence_root.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            _decrypt(encrypted, target, identity)
            target.chmod(0o600)
            if target.stat().st_size != ref["size"] or _sha256(target) != ref["sha256"]:
                raise BackupError("restored_evidence_mismatch")
        _run(["pg_restore", "--exit-on-error", "--single-transaction", "--no-owner", "--no-acl",
              "--dbname", env["PGDATABASE"], str(clear_archive)], env=env)
    return _read_only_validation(env, evidence_root, manifest)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local encrypted backup and isolated restore only")
    parser.add_argument("operation", choices=("backup", "restore-test"))
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path)
    parser.add_argument("--backup-directory", type=Path)
    parser.add_argument("--identity", type=Path)
    args = parser.parse_args()
    try:
        if args.operation == "backup":
            if args.staging_root is None:
                raise BackupError("staging_root_missing")
            result = create_backup(args.staging_root, args.evidence_root)
            print(json.dumps({"status": "COMPLETE_LOCAL", "backup_id": result.name}))
        else:
            if args.backup_directory is None or args.identity is None:
                raise BackupError("restore_inputs_missing")
            print(json.dumps(restore_isolated(args.backup_directory, args.evidence_root, args.identity)))
    except Exception:
        # No raw tool output, connection string, path or secret is ever logged.
        print(json.dumps({"status": "FAILED", "error_code": "data_protection_failed"}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
