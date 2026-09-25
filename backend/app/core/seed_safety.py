"""Safety gates and external secret loading for manual seed scripts."""

from __future__ import annotations

import os
from pathlib import Path

from app.core.config import settings


def require_seed_execution(
    *,
    seed_name: str,
    confirmation_env: str,
    allowed_non_production_envs: set[str],
) -> None:
    """Require a permitted environment and the seed's exact manual opt-in."""
    app_env = settings.app_env.strip().lower()
    if app_env == "production":
        raise RuntimeError("Seed execution is forbidden in production.")

    allowed = {environment.strip().lower() for environment in allowed_non_production_envs}
    if app_env not in allowed:
        raise RuntimeError("Seed execution is not allowed in this environment.")

    expected_confirmation = {
        "standard": "I_UNDERSTAND_THIS_CREATES_AN_ADMIN_ACCOUNT",
        "homologation": "I_UNDERSTAND_THIS_WRITES_FAKE_FINANCIAL_DATA",
    }.get(seed_name)
    if expected_confirmation is None or os.getenv(confirmation_env) != expected_confirmation:
        raise RuntimeError("Explicit seed opt-in is required.")


def read_required_seed_secret(env_name: str) -> str:
    """Read a non-empty seed password from its environment variable or file."""
    file_name = os.getenv(f"{env_name}_FILE")
    if file_name is not None:
        try:
            secret = Path(file_name).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            raise RuntimeError("Required seed secret could not be read.") from None
    else:
        secret = os.getenv(env_name)
        if secret is None:
            raise RuntimeError("Required seed secret is not configured.")

    if not secret.strip():
        raise RuntimeError("Required seed secret is empty.")
    return secret
