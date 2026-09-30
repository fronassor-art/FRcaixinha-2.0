"""Fail-closed guards for the synthetic backup/restore harness."""

from pathlib import Path

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
