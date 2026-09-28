import os
from pathlib import Path
import subprocess

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET", "test-only-runtime-config-secret")
os.environ.setdefault("APP_ENV", "test")

from app.core.config import Settings, validate_runtime_settings


def production_settings(**overrides):
    values = {
        "database_url": "postgresql+psycopg://dbuser:dbpass@db.example.internal/frcaixinha",
        "jwt_secret": "a-test-only-secret-value",
        "app_env": "production",
        "allowed_hosts": "api.internal.example",
        "cors_origins": "https://client.internal.example",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_production_accepts_supported_postgresql_driver():
    validate_runtime_settings(production_settings())


def test_production_rejects_sqlite():
    with pytest.raises(RuntimeError, match=r"postgresql\+psycopg"):
        validate_runtime_settings(production_settings(database_url="sqlite:///prod.db"))


def test_development_continues_to_accept_sqlite():
    config = production_settings(database_url="sqlite:///dev.db", app_env="development")
    validate_runtime_settings(config)


@pytest.mark.parametrize(
    ("database_url", "jwt_secret"),
    [
        ("", "configured-secret"),
        ("postgresql+psycopg://user:pass@db/app", ""),
    ],
)
def test_required_database_and_jwt_settings_remain_required(database_url, jwt_secret):
    with pytest.raises(RuntimeError, match="DATABASE_URL/JWT_SECRET"):
        validate_runtime_settings(
            production_settings(database_url=database_url, jwt_secret=jwt_secret)
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("allowed_hosts", "*"), ("allowed_hosts", ""), ("cors_origins", "*"), ("cors_origins", "")],
)
def test_production_still_rejects_wildcard_or_empty_hosts_and_cors(field, value):
    with pytest.raises(RuntimeError, match="ALLOWED_HOSTS|CORS_ORIGINS"):
        validate_runtime_settings(production_settings(**{field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("jwt_secret", "change_me"),
        ("mercado_pago_access_token", "CHANGE_ME"),
        ("mercado_pago_webhook_secret", "change_me"),
        ("database_url", "postgresql+psycopg://user:CHANGE_ME_DB_PASSWORD@db/app"),
    ],
)
def test_production_rejects_documented_secret_placeholders(field, value):
    with pytest.raises(RuntimeError, match="placeholder"):
        validate_runtime_settings(production_settings(**{field: value}))


def test_production_rejects_documented_database_url_placeholder():
    with pytest.raises(RuntimeError, match="placeholder"):
        validate_runtime_settings(production_settings(database_url="change_me"))


def test_documented_placeholder_values_are_not_rejected_in_development():
    validate_runtime_settings(
        production_settings(
            database_url="change_me",
            jwt_secret="change_me",
            mercado_pago_access_token="CHANGE_ME",
            app_env="development",
        )
    )


def test_container_start_script_uses_port_and_falls_back_to_8000(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "fastapi"
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n', encoding="utf-8")
    stub.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{stub_dir}{os.pathsep}{env.get('PATH', '')}"
    env.pop("PORT", None)

    default_run = subprocess.run(
        ["sh", str(backend / "start.sh")], cwd=backend, env=env,
        check=True, capture_output=True, text=True,
    )
    assert default_run.stdout.splitlines() == [
        "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000"
    ]

    env["PORT"] = "9123"
    port_run = subprocess.run(
        ["sh", str(backend / "start.sh")], cwd=backend, env=env,
        check=True, capture_output=True, text=True,
    )
    assert port_run.stdout.splitlines()[-2:] == ["--port", "9123"]
