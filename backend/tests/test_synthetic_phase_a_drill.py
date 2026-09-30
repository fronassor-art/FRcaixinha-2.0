"""Fail-closed guards for the synthetic backup/restore harness."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import synthetic_phase_a_drill as drill


def test_inherited_database_url_file_is_rejected():
    with pytest.raises(drill.DrillError, match="inherited_database_url_file_forbidden"):
        drill._safe_environment({"PATH": "/usr/bin", "DATABASE_URL_FILE": "/run/secrets/database_url"})


def test_explicit_url_must_match_pg_target_before_connecting(tmp_path, monkeypatch):
    passfile = tmp_path / "pgpass"
    passfile.write_text("synthetic-only\n")
    passfile.chmod(0o600)
    base = drill._safe_environment({"PATH": "/usr/bin"})
    database = "frcaixinha_synthetic_123456abcdef"
    user = database
    env = drill._target_env(base, user=user, database=database, port=32768,
                            passfile=passfile)
    monkeypatch.setattr(drill.psycopg, "connect", lambda **kwargs: pytest.fail("connected"))
    env["DATABASE_URL"] = env["DATABASE_URL"].replace(":32768/", ":5432/")
    with pytest.raises(drill.DrillError, match="database_url_pg_target_mismatch"):
        drill._preflight(env, database=database, user=user, port=32768,
                         container_id="synthetic", docker_env={}, restore=False)


def test_production_host_and_database_rejected_before_connecting(tmp_path, monkeypatch):
    passfile = tmp_path / "pgpass"
    passfile.write_text("synthetic-only\n")
    passfile.chmod(0o600)
    base = drill._safe_environment({"PATH": "/usr/bin"})
    env = drill._target_env(base, user="production", database="frcaixinha",
                            port=5432, passfile=passfile)
    env["PGHOST"] = "production.example.invalid"
    monkeypatch.setattr(drill.psycopg, "connect", lambda **kwargs: pytest.fail("connected"))
    with pytest.raises(drill.DrillError, match="synthetic_database_target_invalid"):
        drill._preflight(env, database="frcaixinha", user="production", port=5432,
                         container_id="not-owned", docker_env={}, restore=False)


def test_restore_requires_test_marker_before_connecting(tmp_path, monkeypatch):
    passfile = tmp_path / "pgpass"
    passfile.write_text("synthetic-only\n")
    passfile.chmod(0o600)
    base = drill._safe_environment({"PATH": "/usr/bin"})
    database = "frcaixinha_restore_123456abcdef"
    env = drill._target_env(base, user="frcaixinha_synthetic_123456abcdef",
                            database=database, port=32768, passfile=passfile)
    env.pop("FRCAIXINHA_ISOLATED_RESTORE")
    monkeypatch.setattr(drill.psycopg, "connect", lambda **kwargs: pytest.fail("connected"))
    with pytest.raises(drill.DrillError, match="synthetic_database_target_invalid"):
        drill._preflight(env, database=database, user=env["PGUSER"], port=32768,
                         container_id="synthetic", docker_env={}, restore=True)


def test_server_identity_must_match_owned_container(tmp_path, monkeypatch):
    passfile = tmp_path / "pgpass"
    passfile.write_text("synthetic-only\n")
    passfile.chmod(0o600)
    database = "frcaixinha_synthetic_123456abcdef"
    env = drill._target_env(drill._safe_environment({"PATH": "/usr/bin"}),
                            user=database, database=database, port=32768,
                            passfile=passfile)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query):
            return self

        def fetchone(self):
            return ("160000", database, database, "111")

    monkeypatch.setattr(drill.psycopg, "connect", lambda **kwargs: FakeConnection())
    monkeypatch.setattr(drill, "_docker_identity", lambda *args, **kwargs: "222")
    with pytest.raises(drill.DrillError, match="synthetic_server_identity_mismatch"):
        drill._preflight(env, database=database, user=database, port=32768,
                         container_id="owned-container", docker_env={}, restore=False)


def test_package_and_identity_roots_must_be_private_and_outside_repo(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    assert drill._private_root(private) == private.resolve()
    private.chmod(0o755)
    with pytest.raises(drill.DrillError, match="private_output_root_invalid"):
        drill._private_root(private)
    with pytest.raises(drill.DrillError, match="private_output_root_invalid"):
        drill._private_root(Path(drill.REPOSITORY))


def test_drill_failure_reports_safe_phase_without_exception_text(tmp_path, monkeypatch):
    package_root = tmp_path / "packages"
    identity_root = tmp_path / "identity"
    package_root.mkdir(mode=0o700)
    identity_root.mkdir(mode=0o700)
    socket = type("Socket", (), {"is_socket": lambda self: True})()
    monkeypatch.setattr(drill, "DOCKER_SOCKET", socket)
    monkeypatch.setattr(drill, "_assert_tools", lambda env: {
        "age_version": "age 1.2.1", "pg_dump_version": "pg_dump (PostgreSQL) 16.0",
        "pg_restore_version": "pg_restore (PostgreSQL) 16.0",
        "age_keygen_version": "age-keygen 1.2.1",
    })
    monkeypatch.setattr(
        drill, "_docker",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("secret-password")),
    )

    with pytest.raises(drill.DrillError) as failure:
        drill.run_drill(package_root, identity_root)

    assert str(failure.value) == "container_start:RuntimeError"
    assert "secret-password" not in str(failure.value)


def test_required_operator_recipient_absent_fails_before_tools_or_docker(tmp_path, monkeypatch):
    package_root = tmp_path / "packages"
    identity_root = tmp_path / "identity"
    package_root.mkdir(mode=0o700)
    identity_root.mkdir(mode=0o700)
    monkeypatch.setattr(drill, "_assert_tools", lambda env: pytest.fail("tools invoked"))
    monkeypatch.setattr(drill, "_docker", lambda *args, **kwargs: pytest.fail("docker invoked"))
    with pytest.raises(drill.DrillError, match="operator_recipient_missing"):
        drill.run_drill(package_root, identity_root, require_operator_recipient=True)
    assert list(package_root.iterdir()) == []
    assert list(identity_root.iterdir()) == []


def test_malformed_operator_recipient_fails_before_container_start(tmp_path, monkeypatch):
    package_root = tmp_path / "packages"
    identity_root = tmp_path / "identity"
    package_root.mkdir(mode=0o700)
    identity_root.mkdir(mode=0o700)
    monkeypatch.setattr(drill, "_assert_tools", lambda env: {"age_version": "age 1.2.1"})
    monkeypatch.setattr(drill.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    monkeypatch.setattr(drill, "_docker", lambda *args, **kwargs: pytest.fail("docker invoked"))
    with pytest.raises(drill.DrillError, match="operator_recipient_invalid"):
        drill.run_drill(package_root, identity_root, operator_recipient="age1malformed")
    assert list(package_root.iterdir()) == []
    assert list(identity_root.iterdir()) == []


def test_publication_contains_only_verified_envelope_and_nonsecret_receipt(tmp_path, monkeypatch):
    envelope = tmp_path / "source.fr"
    envelope.write_bytes(b"encrypted-envelope-bytes")
    publish_root = tmp_path / "publish"
    publish_root.mkdir(mode=0o700)
    publish_root.chmod(0o700)
    backup_id = "20260930T120000Z-" + "a" * 32
    monkeypatch.setattr(drill.backup_transport, "inspect_envelope",
                        lambda path: {"backup_id": backup_id})
    metadata = {"sha256": hashlib.sha256(envelope.read_bytes()).hexdigest(),
                "size": envelope.stat().st_size}
    private_key_marker = "AGE-SECRET-KEY-NEVER-PUBLISH"
    manifest = {"status": "COMPLETE_LOCAL", "off_vm_status": "NOT_UPLOADED",
                "evidence_file_count": 1,
                "alembic_head": drill.data_protection.EXPECTED_HEAD,
                "postgres_version": "16.0", "pg_dump_version": "pg_dump (PostgreSQL) 16.0"}
    restore = {"alembic_head": drill.data_protection.EXPECTED_HEAD,
               "ledger": "PASS", "reconciliation": "PASS", "evidence_files": 1}
    versions = {"pg_restore_version": "pg_restore (PostgreSQL) 16.0"}
    evidence_sha256 = hashlib.sha256(b"synthetic-only").hexdigest()

    artifact, receipt_path = drill._write_publication(
        envelope, publish_root, backup_id=backup_id, manifest=manifest,
        envelope_metadata=metadata, restore=restore, versions=versions,
        evidence_sha256=evidence_sha256)

    assert artifact.read_bytes() == envelope.read_bytes()
    assert artifact.stat().st_mode & 0o077 == 0
    assert receipt_path.stat().st_mode & 0o077 == 0
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "COMPLETE_LOCAL"
    assert receipt["off_vm_status"] == "NOT_UPLOADED"
    assert receipt["envelope_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert receipt["envelope_size"] == artifact.stat().st_size
    assert receipt["synthetic_evidence_sha256"] == evidence_sha256
    assert set(path.name for path in publish_root.iterdir()) == {artifact.name, receipt_path.name}
    assert private_key_marker.encode() not in artifact.read_bytes()
    assert private_key_marker not in receipt_path.read_text()


def test_manual_artifact_workflow_is_main_only_and_never_selects_identity_files():
    workflow = (Path(drill.REPOSITORY) / ".github/workflows/synthetic-phase-a-persistent.yml").read_text()
    assert "workflow_dispatch:" in workflow
    assert '"refs/heads/main"' in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "vars.BACKUP_AGE_OPERATOR_RECIPIENT" in workflow
    assert "secrets." not in workflow
    assert "--require-operator-recipient" in workflow
    upload_step = workflow.split("- name: Upload verified encrypted envelope and receipt", 1)[1]
    assert "steps.drill.outputs.envelope_path" in upload_step
    assert "steps.drill.outputs.receipt_path" in upload_step
    assert "identity_root" not in upload_step
    assert "package_root" not in upload_step
    assert "retention-days: 7" in upload_step
    assert "google-drive-token" not in workflow


def test_database_error_diagnostic_uses_safe_category_only():
    error = drill.psycopg.OperationalError(
        "password authentication failed for user synthetic secret-password"
    )
    assert drill._safe_pg_error_code(error) == "password_authentication_failed"


def test_published_database_endpoint_is_retried_until_reachable(monkeypatch):
    attempts = iter([
        drill.DrillError("synthetic_database_connect_failed:OperationalError"),
        "synthetic-system-id",
    ])

    def preflight(*args, **kwargs):
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(drill, "_preflight", preflight)
    monkeypatch.setattr(drill.time, "sleep", lambda _: None)
    assert drill._wait_for_preflight({}, database="synthetic", user="synthetic", port=12345,
                                     container_id="owned", docker_env={}) == "synthetic-system-id"
