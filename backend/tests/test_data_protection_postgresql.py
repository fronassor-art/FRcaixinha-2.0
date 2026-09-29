"""Real PostgreSQL 16 and age contracts; CI supplies only synthetic credentials."""

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from app import data_protection as protection

psycopg = pytest.importorskip("psycopg")


pytestmark = pytest.mark.skipif(
    not (os.environ.get("DATABASE_URL", "").startswith("postgresql")
         and shutil.which("pg_dump") and shutil.which("pg_restore") and shutil.which("age")
         and shutil.which("age-keygen")),
    reason="PostgreSQL 16 client, age and DATABASE_URL PostgreSQL required",
)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    for name, default in (("PGHOST", "127.0.0.1"), ("PGPORT", "5432"),
                          ("PGUSER", "frcaixinha_test"), ("PGDATABASE", "frcaixinha_test")):
        monkeypatch.setenv(name, os.environ.get(name, default))
    password = tmp_path / "pgpass"
    password.write_text("*:*:*:frcaixinha_test:frcaixinha_test\n")
    password.chmod(0o600)
    monkeypatch.setenv("PGPASSFILE", str(password))
    monkeypatch.delenv("PGPASSWORD", raising=False)
    identity = tmp_path / "identity.txt"
    subprocess.run(["age-keygen", "-o", str(identity)], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=True)
    recipient = subprocess.check_output(["age-keygen", "-y", str(identity)], text=True).strip()
    monkeypatch.setenv("BACKUP_AGE_RECIPIENT", recipient)
    monkeypatch.setenv("BACKUP_OFF_VM_PROVIDER", "google_drive")
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "1dXpu50BErONnQRPBdQ5ND1EUVAyRGsqU")
    monkeypatch.setenv("BACKUP_APPLICATION_COMMIT", "a" * 40)
    stage = tmp_path / "stage"
    stage.mkdir()
    source_evidence = tmp_path / "source-evidence"
    source_evidence.mkdir()
    return stage, source_evidence, identity


def _manifest(directory):
    return json.loads((directory / "manifest.json").read_text())


def test_backup_restore_postgresql_16_custom_archive_and_read_only_validation(setup, tmp_path, monkeypatch):
    stage, source_evidence, identity = setup
    source_db = os.environ["PGDATABASE"]
    with protection._pg_connect(protection._pg_env()) as conn:
        conn.execute("SELECT setval(pg_get_serial_sequence('ledger_entries', 'id'), 47, true)")
    directory = protection.create_backup(stage, source_evidence)
    manifest = _manifest(directory)
    assert manifest["status"] == "COMPLETE_LOCAL"
    assert manifest["archive_format"] == "custom"
    assert manifest["archive"]["filename"] == "postgres.dump.age"
    assert manifest["archive"]["sha256"] == protection._sha256(directory / "postgres.dump.age")
    assert manifest["off_vm_status"] == "NOT_UPLOADED"
    assert manifest["remote_file_id"] is None
    assert manifest["evidence_file_count"] == 0
    assert "frcaixinha_test" not in json.dumps(manifest)
    restore_db = "frcaixinha_restore_" + uuid.uuid4().hex[:12]
    with protection._pg_connect(protection._pg_env()) as conn:
        conn.execute(f"CREATE DATABASE {restore_db}")
    try:
        monkeypatch.setenv("PGDATABASE", restore_db)
        monkeypatch.setenv("FRCAIXINHA_ISOLATED_RESTORE", "YES")
        monkeypatch.setenv("APP_ENV", "test")
        restored_evidence = tmp_path / "restored-evidence"
        restored_evidence.mkdir()
        result = protection.restore_isolated(directory, restored_evidence, identity)
        assert result["alembic_head"] == protection.EXPECTED_HEAD
        assert result["ledger"] == "PASS"
        assert result["evidence_files"] == 0
        with protection._pg_connect(protection._pg_env()) as conn:
            assert conn.execute("SELECT last_value FROM ledger_entries_id_seq").fetchone()[0] == 47
            audit_before = conn.execute("SELECT count(*) FROM audit_logs").fetchone()[0]
        protection._read_only_validation(protection._pg_env(), restored_evidence, manifest)
        with protection._pg_connect(protection._pg_env()) as conn:
            assert conn.execute("SELECT count(*) FROM audit_logs").fetchone()[0] == audit_before
    finally:
        monkeypatch.setenv("PGDATABASE", source_db)
        with protection._pg_connect(protection._pg_env()) as conn:
            conn.execute(f"DROP DATABASE {restore_db} WITH (FORCE)")


def test_missing_or_wrong_evidence_never_completes(setup, monkeypatch):
    stage, source_evidence, _ = setup
    expected = "0" * 64
    ref = {"table": "workflow_execution_evidence_files", "row_id": 7,
           "path": "7/missing.bin", "size": 7, "sha256": expected}
    monkeypatch.setattr(protection, "_evidence_refs", lambda conn: [ref])
    with pytest.raises(protection.BackupError):
        protection.create_backup(stage, source_evidence)
    failed = next(stage.iterdir())
    assert _manifest(failed)["status"] == "FAILED"
    (source_evidence / "7").mkdir()
    (source_evidence / "7/missing.bin").write_bytes(b"wrong!!")
    with pytest.raises(protection.BackupError, match="evidence_content_mismatch"):
        protection.create_backup(stage, source_evidence)
    assert all(_manifest(directory)["status"] == "FAILED" for directory in stage.iterdir())


def test_pg_dump_command_has_no_password_or_database_url(setup, monkeypatch):
    stage, source_evidence, _ = setup
    commands = []
    original = protection.subprocess.Popen

    def capture(arguments, *args, **kwargs):
        commands.append(arguments)
        return original(arguments, *args, **kwargs)

    monkeypatch.setattr(protection.subprocess, "Popen", capture)
    protection.create_backup(stage, source_evidence)
    dump_command = next(command for command in commands if command[0] == "pg_dump" and "--snapshot" in command)
    assert "--snapshot" in dump_command
    assert all("password" not in argument.lower() and "frcaixinha_test:frcaixinha_test" not in argument
               for argument in dump_command)
    assert all("postgresql://" not in argument for argument in dump_command)


def test_archive_corruption_and_truncation_rejected_before_restore(setup, tmp_path, monkeypatch):
    stage, source_evidence, identity = setup
    directory = protection.create_backup(stage, source_evidence)
    archive = directory / "postgres.dump.age"
    restore_db = "frcaixinha_restore_" + uuid.uuid4().hex[:12]
    with protection._pg_connect(protection._pg_env()) as conn:
        conn.execute(f"CREATE DATABASE {restore_db}")
    try:
        monkeypatch.setenv("PGDATABASE", restore_db)
        monkeypatch.setenv("FRCAIXINHA_ISOLATED_RESTORE", "YES")
        monkeypatch.setenv("APP_ENV", "test")
        restored_evidence = tmp_path / "restored-evidence"
        restored_evidence.mkdir()
        original = archive.read_bytes()
        for payload in (original[:-1], original[:-1] + bytes([original[-1] ^ 1])):
            archive.write_bytes(payload)
            with pytest.raises(protection.BackupError, match="archive_checksum_mismatch"):
                protection.restore_isolated(directory, restored_evidence, identity)
            with protection._pg_connect(protection._pg_env()) as conn:
                assert conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0] == 0
    finally:
        with protection._pg_connect({**protection._pg_env(), "PGDATABASE": "frcaixinha_test"}) as conn:
            conn.execute(f"DROP DATABASE {restore_db} WITH (FORCE)")
