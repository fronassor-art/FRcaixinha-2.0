from datetime import date
from decimal import Decimal
import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.exc import ArgumentError
from sqlalchemy.engine import make_url


def _read_file_env(field: str) -> str | None:
    """Read FIELD from FIELD_FILE when present, without logging the secret."""
    path = os.getenv(f"{field.upper()}_FILE")
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        raise RuntimeError(f"Secret file not found for {field}: {path}")
    return p.read_text(encoding="utf-8").strip()


class Settings(BaseSettings):
    database_url: str = ""
    jwt_secret: str = ""
    payout_destination_encryption_key: str | None = None
    access_token_minutes: int = 60
    password_reset_minutes: int = 30
    session_idle_minutes: int = 30
    webhook_signature_max_age_seconds: int = 300
    app_env: str = "development"
    mercado_pago_access_token: str | None = None
    mercado_pago_webhook_secret: str | None = None
    mercado_pago_base_url: str = "https://api.mercadopago.com"
    mercado_pago_sandbox_email: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_starttls: bool = True
    password_reset_base_url: str | None = None
    redis_url: str = "redis://redis:6379/0"
    allowed_hosts: str = "*"
    cors_origins: str = "*"
    rate_limit_per_minute: int = 60
    log_level: str = "INFO"
    backup_retention_days: int = 7
    loan_daily_penalty_rate: Decimal = Decimal("0.00")
    loan_late_charge_effective_date: date | None = None
    workflow_evidence_storage_root: str = "./data/workflow-evidence"
    workflow_evidence_max_bytes: int = 10 * 1024 * 1024
    workflow_evidence_allowed_types: str = "application/pdf,image/jpeg,image/png,text/plain,text/csv,application/zip"

    @property
    def allowed_hosts_list(self):
        return [x.strip() for x in self.allowed_hosts.split(",") if x.strip() and x.strip() != "*"]

    @property
    def cors_origins_list(self):
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

# Production deployments may inject sensitive values as Docker/OS secret files.
# *_FILE takes precedence over the corresponding setting when present.
for _field in (
    "database_url", "jwt_secret", "mercado_pago_access_token",
    "mercado_pago_webhook_secret", "smtp_password", "redis_url",
    "payout_destination_encryption_key",
):
    _value = _read_file_env(_field)
    if _value is not None:
        setattr(settings, _field, _value)

_KNOWN_PLACEHOLDERS = {"change_me", "change_me_db_password"}


def validate_runtime_settings(config: Settings) -> None:
    if not config.database_url or not config.jwt_secret:
        raise RuntimeError("DATABASE_URL/JWT_SECRET must be configured directly or via *_FILE secrets")

    if config.app_env.strip().lower() != "production":
        return

    if config.allowed_hosts.strip() in {"", "*"}:
        raise RuntimeError("ALLOWED_HOSTS must be explicit in production")
    if config.cors_origins.strip() in {"", "*"}:
        raise RuntimeError("CORS_ORIGINS must be explicit in production")

    if config.database_url.strip().casefold() in _KNOWN_PLACEHOLDERS:
        raise RuntimeError("DATABASE_URL contains a documented placeholder in production")

    try:
        database_url = make_url(config.database_url)
    except (ArgumentError, ValueError):
        raise RuntimeError("DATABASE_URL must be a valid PostgreSQL URL in production") from None
    if database_url.drivername != "postgresql+psycopg":
        raise RuntimeError("DATABASE_URL must use the supported postgresql+psycopg driver in production")
    if database_url.password and database_url.password.casefold() in _KNOWN_PLACEHOLDERS:
        raise RuntimeError("DATABASE_URL contains a documented placeholder in production")

    for value in (
        config.jwt_secret,
        config.mercado_pago_access_token,
        config.mercado_pago_webhook_secret,
        config.smtp_password,
        config.payout_destination_encryption_key,
    ):
        if value is not None and value.strip().casefold() in _KNOWN_PLACEHOLDERS:
            raise RuntimeError("A configured production secret contains a documented placeholder")


validate_runtime_settings(settings)
