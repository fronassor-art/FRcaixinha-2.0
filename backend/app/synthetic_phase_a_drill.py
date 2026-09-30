"""Produce a synthetic Phase A set using a self-owned PostgreSQL 16 container.

No caller-supplied database address or credential is accepted. This module does
not start the application or worker and never contacts Google Drive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from app import backup_transport, data_protection


IMAGE = "postgres:16-alpine"
FOLDER_ID = "1dXpu50BErONnQRPBdQ5ND1EUVAyRGsqU"
DOCKER_SOCKET = Path("/var/run/docker.sock")
BACKEND = Path(__file__).resolve().parents[1]
REPOSITORY = BACKEND.parent


class DrillError(RuntimeError):
    """Stable, non-sensitive failure code."""


def _safe_pg_error_code(error: psycopg.Error) -> str:
    if error.sqlstate:
        return error.sqlstate
    message = str(error).lower()
    for fragment, code in (
        ("connection refused", "connection_refused"),
        ("timeout expired", "connection_timeout"),
        ("password authentication failed", "password_authentication_failed"),
        ("no password supplied", "password_missing"),
        ("no pg_hba.conf entry", "pg_hba_rejected"),
        ("could not translate host name", "host_resolution_failed"),
    ):
        if fragment in message:
            return code
    return type(error).__name__


def _command(arguments: list[str], *, env: dict[str, str], cwd: Path | None = None) -> str:
    result = subprocess.run(arguments, env=env, cwd=cwd, capture_output=True, text=True,
                            check=False)
    if result.returncode:
        raise DrillError("synthetic_drill_command_failed")
    return result.stdout.strip()


def _private_root(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise DrillError("private_output_root_invalid")
    resolved = path.resolve(strict=True)
    if resolved.is_relative_to(REPOSITORY) or path.stat().st_mode & 0o077:
        raise DrillError("private_output_root_invalid")
    return resolved


def _safe_environment(base: dict[str, str]) -> dict[str, str]:
    if base.get("DATABASE_URL_FILE"):
        raise DrillError("inherited_database_url_file_forbidden")
    return {"PATH": base.get("PATH", ""), "APP_ENV": "test",
            "JWT_SECRET": "synthetic-drill-only", "PYTHONPATH": str(BACKEND),
            "FRCAIXINHA_ISOLATED_RESTORE": "YES"}


def _docker(arguments: list[str], *, env: dict[str, str]) -> str:
    return _command(["docker", "--host", "unix:///var/run/docker.sock", *arguments], env=env)


def _docker_identity(container_id: str, user: str, database: str,
                     *, env: dict[str, str]) -> str:
    return _docker(["exec", container_id, "psql", "-U", user, "-d", database,
                    "-Atqc", "SELECT system_identifier FROM pg_control_system()"], env=env)


def _preflight(env: dict[str, str], *, database: str, user: str, port: int,
               container_id: str, docker_env: dict[str, str], restore: bool) -> str:
    if (env.get("APP_ENV") != "test" or "DATABASE_URL_FILE" in env
            or "PGPASSWORD" in env or env.get("PGHOST") != "127.0.0.1"
            or env.get("PGPORT") != str(port) or env.get("PGUSER") != user
            or env.get("PGDATABASE") != database
            or (restore and env.get("FRCAIXINHA_ISOLATED_RESTORE") != "YES")
            or not re.fullmatch(r"frcaixinha_(?:synthetic|restore)_[0-9a-f]{12}", database)):
        raise DrillError("synthetic_database_target_invalid")
    url = make_url(env.get("DATABASE_URL", ""))
    if (url.drivername != "postgresql+psycopg" or url.host != "127.0.0.1"
            or url.port != port or url.username != user or url.database != database
            or url.password is not None):
        raise DrillError("database_url_pg_target_mismatch")
    passfile = Path(env.get("PGPASSFILE", ""))
    if (not passfile.is_file() or passfile.is_symlink()
            or passfile.stat().st_mode & 0o077):
        raise DrillError("synthetic_passfile_invalid")
    try:
        conn = psycopg.connect(host="127.0.0.1", port=port, user=user, dbname=database,
                               passfile=str(passfile), autocommit=True,
                               connect_timeout=5)
    except psycopg.Error as exc:
        code = _safe_pg_error_code(exc)
        raise DrillError(f"synthetic_database_connect_failed:{code}") from None
    try:
        with conn:
            version, actual_db, actual_user, system_id = conn.execute(
                "SELECT current_setting('server_version_num'), current_database(), "
                "current_user, system_identifier FROM pg_control_system()"
            ).fetchone()
    except psycopg.Error as exc:
        code = _safe_pg_error_code(exc)
        raise DrillError(f"synthetic_database_identity_query_failed:{code}") from None
    if (int(version) // 10000 != 16 or actual_db != database or actual_user != user
            or str(system_id) != _docker_identity(container_id, user, database,
                                                   env=docker_env)):
        raise DrillError("synthetic_server_identity_mismatch")
    return str(system_id)


def _assert_tools(env: dict[str, str]) -> str:
    def version(tool: str) -> str:
        result = subprocess.run([tool, "--version"], env=env, capture_output=True,
                                text=True, check=False)
        if result.returncode:
            raise DrillError("synthetic_tool_version_invalid")
        return (result.stdout + result.stderr).strip()

    dump_version = version("pg_dump")
    restore_version = version("pg_restore")
    age_version = version("age")
    keygen_version = version("age-keygen")
    if (not dump_version.startswith("pg_dump (PostgreSQL) 16.")
            or not restore_version.startswith("pg_restore (PostgreSQL) 16.")
            or "1.2.1" not in age_version or "1.2.1" not in keygen_version):
        raise DrillError("synthetic_tool_version_invalid")
    return age_version


def _synthetic_evidence(env: dict[str, str], evidence_root: Path) -> str:
    payload = b"FRCAIXINHA SYNTHETIC RESTORE EVIDENCE ONLY\n"
    digest = hashlib.sha256(payload).hexdigest()
    with psycopg.connect(host=env["PGHOST"], port=env["PGPORT"],
                         user=env["PGUSER"], dbname=env["PGDATABASE"],
                         passfile=env["PGPASSFILE"], autocommit=True) as conn:
        token = secrets.token_hex(8)
        user_id = conn.execute(
            "INSERT INTO users(name,email,cpf,password_hash,role,is_active,is_master,created_at) "
            "VALUES (%s,%s,%s,%s,'ADMIN',true,false,now()) RETURNING id",
            ("Synthetic restore only", f"synthetic-{token}@example.invalid",
             f"{secrets.randbelow(10**11):011d}", "synthetic-not-a-login"),
        ).fetchone()[0]
        task_id = conn.execute(
            "INSERT INTO operational_workflow_tasks(action_code,status,priority,created_by,"
            "created_at,updated_at,sla_status,escalation_level) "
            "VALUES ('CI_BACKUP','PENDING','MEDIUM',%s,now(),now(),'ON_TRACK','NONE') RETURNING id",
            (user_id,),
        ).fetchone()[0]
        evidence_id = conn.execute(
            "INSERT INTO workflow_execution_evidence(task_id,added_by,evidence_type,"
            "content,content_hash,created_at) VALUES (%s,%s,'ATTACHMENT','synthetic',%s,now()) RETURNING id",
            (task_id, user_id, hashlib.sha256(b"synthetic").hexdigest()),
        ).fetchone()[0]
        key = f"{task_id}/{token}.txt"
        conn.execute(
            "INSERT INTO workflow_execution_evidence_files(evidence_id,version,original_name,"
            "storage_key,content_type,size_bytes,sha256,uploaded_by,created_at) "
            "VALUES (%s,1,'synthetic.txt',%s,'text/plain',%s,%s,%s,now())",
            (evidence_id, key, len(payload), digest, user_id),
        )
    target = evidence_root / key
    target.parent.mkdir(mode=0o700)
    target.write_bytes(payload)
    target.chmod(0o600)
    return key


def _target_env(base: dict[str, str], *, user: str, database: str, port: int,
                passfile: Path) -> dict[str, str]:
    return {**base, "DATABASE_URL": f"postgresql+psycopg://{user}@127.0.0.1:{port}/{database}",
            "PGHOST": "127.0.0.1", "PGPORT": str(port), "PGUSER": user,
            "PGDATABASE": database, "PGPASSFILE": str(passfile)}


def run_drill(package_root: Path, identity_root: Path) -> dict:
    package_root = _private_root(package_root)
    identity_root = _private_root(identity_root)
    if (identity_root == package_root or identity_root.is_relative_to(package_root)
            or package_root.is_relative_to(identity_root)):
        raise DrillError("identity_must_be_separate")
    if not DOCKER_SOCKET.is_socket():
        raise DrillError("local_docker_socket_required")
    base = _safe_environment(os.environ)
    age_version = _assert_tools(base)
    nonce = secrets.token_hex(6)
    source_db = f"frcaixinha_synthetic_{nonce}"
    restore_db = f"frcaixinha_restore_{nonce}"
    user = f"frcaixinha_synthetic_{nonce}"
    password = secrets.token_urlsafe(24)
    container_name = f"frcaixinha-synthetic-{nonce}"
    stage = package_root / f".stage-{nonce}"
    stage.mkdir(mode=0o700)
    identity = identity_root / f".identity-{nonce}.tmp"
    container_id = ""
    complete = False
    phase = "container_start"
    try:
        with tempfile.TemporaryDirectory(prefix="frcaixinha-synthetic-") as temporary:
            work = Path(temporary)
            work.chmod(0o700)
            docker_config = work / "docker-config"
            docker_config.mkdir(mode=0o700)
            docker_env = {"PATH": base["PATH"], "HOME": str(work),
                          "DOCKER_CONFIG": str(docker_config),
                          "POSTGRES_USER": user, "POSTGRES_PASSWORD": password,
                          "POSTGRES_DB": source_db}
            container_id = _docker(["run", "--detach", "--rm", "--pull=never",
                                    "--name", container_name,
                                    "--label", f"frcaixinha.synthetic={nonce}",
                                    "--publish", "127.0.0.1::5432",
                                    "--env", "POSTGRES_USER", "--env", "POSTGRES_PASSWORD",
                                    "--env", "POSTGRES_DB", IMAGE], env=docker_env)
            phase = "container_identity"
            inspected = json.loads(_docker(["inspect", container_id], env=docker_env))[0]
            ports = inspected["NetworkSettings"]["Ports"]["5432/tcp"]
            if (inspected["Id"] != container_id or inspected["Config"]["Image"] != IMAGE
                    or inspected["Config"]["Labels"].get("frcaixinha.synthetic") != nonce
                    or len(ports) != 1 or ports[0]["HostIp"] != "127.0.0.1"):
                raise DrillError("synthetic_container_identity_invalid")
            port = int(ports[0]["HostPort"])
            if not 1024 <= port <= 65535:
                raise DrillError("synthetic_container_port_invalid")
            phase = "postgres_startup"
            for _ in range(60):
                try:
                    _docker(["exec", container_id, "pg_isready", "-U", user, "-d", source_db],
                            env=docker_env)
                    break
                except DrillError:
                    time.sleep(1)
            else:
                raise DrillError("synthetic_postgres_not_ready")
            phase = "database_preflight"
            passfile = work / "pgpass"
            passfile.write_text(f"127.0.0.1:{port}:*:{user}:{password}\n")
            passfile.chmod(0o600)
            source_env = _target_env(base, user=user, database=source_db, port=port,
                                     passfile=passfile)
            system_id = _preflight(source_env, database=source_db, user=user, port=port,
                                   container_id=container_id, docker_env=docker_env, restore=False)
            phase = "age_identity"
            _command(["age-keygen", "-o", str(identity)], env=base)
            identity.chmod(0o600)
            recipient = _command(["age-keygen", "-y", str(identity)], env=base)
            if not recipient.startswith("age1"):
                raise DrillError("synthetic_age_recipient_invalid")
            alembic_config = work / "alembic.ini"
            alembic_config.write_text(
                "[alembic]\nscript_location = " + str(BACKEND / "alembic")
                + "\nprepend_sys_path = " + str(BACKEND) + "\n"
            )
            alembic_config.chmod(0o600)
            _preflight(source_env, database=source_db, user=user, port=port,
                       container_id=container_id, docker_env=docker_env, restore=False)
            phase = "alembic_upgrade"
            _command([sys.executable, "-m", "alembic", "-c", str(alembic_config),
                      "upgrade", "head"], env=source_env, cwd=work)
            with psycopg.connect(host="127.0.0.1", port=port, user=user,
                                 dbname=source_db, passfile=str(passfile)) as conn:
                head = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            if head != data_protection.EXPECTED_HEAD:
                raise DrillError("synthetic_alembic_head_invalid")
            phase = "synthetic_evidence"
            evidence_root = work / "source-evidence"
            evidence_root.mkdir(mode=0o700)
            evidence_key = _synthetic_evidence(source_env, evidence_root)
            commit = _command(["git", "rev-parse", "HEAD"],
                              env={"PATH": base["PATH"]}, cwd=REPOSITORY)
            backup_env = {**source_env, "BACKUP_OFF_VM_PROVIDER": "google_drive",
                          "GOOGLE_DRIVE_FOLDER_ID": FOLDER_ID,
                          "BACKUP_AGE_RECIPIENT": recipient,
                          "BACKUP_APPLICATION_COMMIT": commit}
            _preflight(backup_env, database=source_db, user=user, port=port,
                       container_id=container_id, docker_env=docker_env, restore=False)
            phase = "phase_a_backup"
            result = json.loads(_command(
                [sys.executable, "-m", "app.data_protection", "backup",
                 "--staging-root", str(stage), "--evidence-root", str(evidence_root)],
                env=backup_env, cwd=work))
            if result.get("status") != "COMPLETE_LOCAL":
                raise DrillError("synthetic_backup_incomplete")
            phase = "manifest_validation"
            backup_id = result["backup_id"]
            package = stage / backup_id
            manifest = json.loads((package / "manifest.json").read_text())
            if (manifest.get("status") != "COMPLETE_LOCAL"
                    or manifest.get("off_vm_status") != "NOT_UPLOADED"
                    or manifest.get("alembic_head") != data_protection.EXPECTED_HEAD
                    or not manifest.get("postgres_version", "").startswith("16.")
                    or not manifest.get("pg_dump_version", "").startswith("pg_dump (PostgreSQL) 16.")
                    or manifest.get("evidence_file_count") != 1):
                raise DrillError("synthetic_manifest_invalid")
            data_protection._verify_file(package, manifest["archive"])
            for ref in manifest["evidence"]:
                data_protection._verify_file(package / "evidence", ref["encrypted"])
            with psycopg.connect(host="127.0.0.1", port=port, user=user,
                                 dbname=source_db, passfile=str(passfile), autocommit=True) as conn:
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(restore_db)))
            phase = "restore_preflight"
            restore_env = _target_env(base, user=user, database=restore_db, port=port,
                                      passfile=passfile)
            _preflight(restore_env, database=restore_db, user=user, port=port,
                       container_id=container_id, docker_env=docker_env, restore=True)
            restored_evidence = work / "restored-evidence"
            restored_evidence.mkdir(mode=0o700)
            phase = "phase_a_restore"
            restored = json.loads(_command(
                [sys.executable, "-m", "app.data_protection", "restore-test",
                 "--backup-directory", str(package), "--evidence-root", str(restored_evidence),
                 "--identity", str(identity)], env=restore_env, cwd=work))
            if (restored.get("alembic_head") != data_protection.EXPECTED_HEAD
                    or restored.get("ledger") != "PASS"
                    or restored.get("reconciliation") != "PASS"
                    or restored.get("evidence_files") != 1):
                raise DrillError("synthetic_restore_integrity_failed")
            phase = "evidence_validation"
            expected = hashlib.sha256((evidence_root / evidence_key).read_bytes()).hexdigest()
            if hashlib.sha256((restored_evidence / evidence_key).read_bytes()).hexdigest() != expected:
                raise DrillError("synthetic_restored_evidence_mismatch")
            with psycopg.connect(host="127.0.0.1", port=port, user=user,
                                 dbname=restore_db, passfile=str(passfile)) as conn:
                count = conn.execute("SELECT count(*) FROM workflow_execution_evidence_files "
                                     "WHERE storage_key=%s AND sha256=%s",
                                     (evidence_key, expected)).fetchone()[0]
            if count != 1:
                raise DrillError("synthetic_restored_inventory_mismatch")
            phase = "envelope_creation"
            envelope = work / f"{backup_id}.frcaixinha.tar"
            metadata = backup_transport.create_envelope(package, envelope)
            backup_transport.inspect_envelope(envelope)
            final_package = package_root / backup_id
            final_identity = identity_root / f"{backup_id}.agekey"
            if final_package.exists() or final_identity.exists():
                raise DrillError("synthetic_output_exists")
            phase = "preserve_validated_output"
            identity.rename(final_identity)
            try:
                package.rename(final_package)
            except OSError:
                final_identity.rename(identity)
                raise
            complete = True
            return {"status": "PASS", "backup_id": backup_id,
                    "package_path": str(final_package), "identity_path": str(final_identity),
                    "package_size": metadata["size"], "package_sha256": metadata["sha256"],
                    "age_version": age_version, "postgres_version": manifest["postgres_version"],
                    "pg_dump_version": manifest["pg_dump_version"],
                    "alembic_head": manifest["alembic_head"],
                    "off_vm_status": manifest["off_vm_status"],
                    "restore": restored, "server_system_identifier": system_id}
    except DrillError as exc:
        raise DrillError(f"{phase}:{exc}") from None
    except Exception as exc:
        # Avoid echoing exception text, which may contain connection data or
        # command arguments. A phase and exception type are sufficient to
        # locate the failure without disclosing sensitive values.
        raise DrillError(f"{phase}:{type(exc).__name__}") from None
    finally:
        if container_id:
            try:
                _docker(["rm", "--force", container_id],
                        env={"PATH": base["PATH"], "HOME": str(package_root)})
            except DrillError:
                pass
        if stage.exists():
            shutil.rmtree(stage)
        if not complete:
            identity.unlink(missing_ok=True)


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Isolated synthetic Phase A backup/restore drill")
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--identity-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run_drill(args.package_root, args.identity_root)
        print(json.dumps(result, sort_keys=True))
    except DrillError as exc:
        print(json.dumps({"status": "FAILED", "error_code": str(exc)}))
        raise SystemExit(1) from None
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error_code": type(exc).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
