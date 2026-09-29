"""Provider-free and database-free safety contracts for Phase A."""

import hashlib
import json
from pathlib import Path

import pytest

from app import data_protection as protection


def test_google_drive_configuration_is_metadata_only(monkeypatch):
    monkeypatch.setenv("BACKUP_OFF_VM_PROVIDER", "google_drive")
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "1dXpu50BErONnQRPBdQ5ND1EUVAyRGsqU")
    monkeypatch.setenv("BACKUP_AGE_RECIPIENT", "age1publictestrecipient")
    assert protection._config() == (
        "google_drive", "1dXpu50BErONnQRPBdQ5ND1EUVAyRGsqU", "age1publictestrecipient"
    )
    assert not hasattr(protection, "GoogleDriveUploader")


def test_password_file_required_without_password_environment(monkeypatch, tmp_path):
    for name, value in {"PGHOST": "127.0.0.1", "PGPORT": "5432", "PGUSER": "test",
                        "PGDATABASE": "test", "PGPASSFILE": str(tmp_path / "pgpass")}.items():
        monkeypatch.setenv(name, value)
    password_file = tmp_path / "pgpass"
    password_file.write_text("test-only\n")
    password_file.chmod(0o600)
    monkeypatch.delenv("PGPASSWORD", raising=False)
    env = protection._pg_env()
    assert "PGPASSWORD" not in env and "DATABASE_URL" not in env
    monkeypatch.setenv("PGPASSWORD", "test-only")
    with pytest.raises(protection.BackupError, match="pg_connection_config_invalid"):
        protection._pg_env()


def test_manifest_hash_rejects_corruption_and_truncation(tmp_path):
    archive = tmp_path / "postgres.dump.age"
    archive.write_bytes(b"encrypted payload")
    metadata = protection._metadata(archive)
    assert protection._verify_file(tmp_path, metadata) == archive
    archive.write_bytes(b"encrypted payloaD")
    with pytest.raises(protection.BackupError, match="archive_checksum_mismatch"):
        protection._verify_file(tmp_path, metadata)
    archive.write_bytes(b"short")
    with pytest.raises(protection.BackupError, match="archive_checksum_mismatch"):
        protection._verify_file(tmp_path, metadata)


def test_evidence_missing_and_symlink_rejected(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    with pytest.raises(protection.BackupError, match="evidence_missing"):
        protection._open_evidence(evidence, "missing.bin")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"private")
    (evidence / "link.bin").symlink_to(outside)
    with pytest.raises(protection.BackupError):
        protection._open_evidence(evidence, "link.bin")


def test_restore_target_guards_before_database_access(monkeypatch):
    env = {"PGHOST": "127.0.0.1", "PGDATABASE": "frcaixinha_restore_test"}
    monkeypatch.setenv("FRCAIXINHA_ISOLATED_RESTORE", "YES")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MERCADO_PAGO_ACCESS_TOKEN", "dummy-test-token")
    with pytest.raises(protection.BackupError, match="external_credentials_present"):
        protection._assert_restore_target(env)
    monkeypatch.delenv("MERCADO_PAGO_ACCESS_TOKEN")
    env["PGDATABASE"] = "frcaixinha_production"
    with pytest.raises(protection.BackupError, match="restore_target_not_isolated"):
        protection._assert_restore_target(env)
    env["PGDATABASE"] = "frcaixinha_restore_test"
    env["PGHOST"] = "db.production.internal"
    with pytest.raises(protection.BackupError, match="restore_host_not_loopback"):
        protection._assert_restore_target(env)


def test_manifest_write_is_complete_json_and_does_not_include_secrets(tmp_path):
    path = tmp_path / "manifest.json"
    payload = {"manifest_version": 1, "status": "COMPLETE_LOCAL", "archive_sha256": hashlib.sha256(b"a").hexdigest()}
    protection._write_json(path, payload)
    assert json.loads(path.read_text()) == payload
    assert path.stat().st_mode & 0o077 == 0
    assert not (tmp_path / "manifest.tmp").exists()
