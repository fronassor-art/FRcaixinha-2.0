import ast
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from app.core import seed_safety
from app.core.seed_safety import read_required_seed_secret, require_seed_execution
from app import seed as standard_seed
from app import seed_homologacao as homologation_seed


ROOT = Path(__file__).resolve().parents[1]
CONFIRMATIONS = {
    "standard": (
        "FRCAIXINHA_ENABLE_STANDARD_SEED",
        "I_UNDERSTAND_THIS_CREATES_AN_ADMIN_ACCOUNT",
        "FRCAIXINHA_SEED_ADMIN_PASSWORD",
    ),
    "homologation": (
        "FRCAIXINHA_ENABLE_HOMOLOGATION_SEED",
        "I_UNDERSTAND_THIS_WRITES_FAKE_FINANCIAL_DATA",
        "FRCAIXINHA_HOMOLOGATION_PASSWORD",
    ),
}
ALLOWED_ENVS = {"development", "test", "homologation", "staging"}


@pytest.mark.parametrize("seed_name", ["standard", "homologation"])
def test_production_blocks_each_seed_before_session_or_secret_read(monkeypatch, seed_name):
    confirmation_env, confirmation, secret_env = CONFIRMATIONS[seed_name]
    monkeypatch.setattr(seed_safety.settings, "app_env", " production ")
    monkeypatch.setenv(confirmation_env, confirmation)
    monkeypatch.setenv(secret_env, "not-logged-test-secret")
    session_calls = []

    def fail_if_session_opens():
        session_calls.append(True)
        raise AssertionError("SessionLocal must not be called")

    target = standard_seed if seed_name == "standard" else homologation_seed
    monkeypatch.setattr(target, "SessionLocal", fail_if_session_opens)

    with pytest.raises(RuntimeError, match="forbidden in production"):
        target.seed()

    assert session_calls == []


@pytest.mark.parametrize("seed_name", ["standard", "homologation"])
def test_opt_in_is_required_and_must_match_exact_value(monkeypatch, seed_name):
    confirmation_env, confirmation, secret_env = CONFIRMATIONS[seed_name]
    monkeypatch.setattr(seed_safety.settings, "app_env", "development")
    monkeypatch.setenv(secret_env, "external-test-secret")

    with pytest.raises(RuntimeError, match="opt-in"):
        require_seed_execution(
            seed_name=seed_name,
            confirmation_env=confirmation_env,
            allowed_non_production_envs=ALLOWED_ENVS,
        )

    monkeypatch.setenv(confirmation_env, "true")
    with pytest.raises(RuntimeError, match="opt-in"):
        require_seed_execution(
            seed_name=seed_name,
            confirmation_env=confirmation_env,
            allowed_non_production_envs=ALLOWED_ENVS,
        )

    monkeypatch.setenv(confirmation_env, confirmation.lower())
    with pytest.raises(RuntimeError, match="opt-in"):
        require_seed_execution(
            seed_name=seed_name,
            confirmation_env=confirmation_env,
            allowed_non_production_envs=ALLOWED_ENVS,
        )


@pytest.mark.parametrize("seed_name", ["standard", "homologation"])
def test_invalid_environment_blocks_before_sessionlocal(monkeypatch, seed_name):
    confirmation_env, confirmation, secret_env = CONFIRMATIONS[seed_name]
    monkeypatch.setattr(seed_safety.settings, "app_env", "preview")
    monkeypatch.setenv(confirmation_env, confirmation)
    monkeypatch.setenv(secret_env, "external-test-secret")
    session_calls = []
    target = standard_seed if seed_name == "standard" else homologation_seed
    monkeypatch.setattr(target, "SessionLocal", lambda: session_calls.append(True))

    with pytest.raises(RuntimeError, match="not allowed"):
        target.seed()

    assert session_calls == []


@pytest.mark.parametrize("seed_name", ["standard", "homologation"])
def test_missing_opt_in_blocks_seed_before_sessionlocal(monkeypatch, seed_name):
    confirmation_env, _confirmation, secret_env = CONFIRMATIONS[seed_name]
    monkeypatch.setattr(seed_safety.settings, "app_env", "development")
    monkeypatch.delenv(confirmation_env, raising=False)
    monkeypatch.setenv(secret_env, "external-test-secret")
    session_calls = []
    target = standard_seed if seed_name == "standard" else homologation_seed
    monkeypatch.setattr(target, "SessionLocal", lambda: session_calls.append(True))

    with pytest.raises(RuntimeError, match="opt-in"):
        target.seed()

    assert session_calls == []


@pytest.mark.parametrize("env_name", [
    "FRCAIXINHA_SEED_ADMIN_PASSWORD",
    "FRCAIXINHA_HOMOLOGATION_PASSWORD",
])
def test_required_secret_missing_or_empty_fails_without_echo(monkeypatch, env_name):
    marker = "secret-that-must-not-appear"
    monkeypatch.delenv(env_name, raising=False)
    monkeypatch.delenv(f"{env_name}_FILE", raising=False)
    with pytest.raises(RuntimeError) as missing:
        read_required_seed_secret(env_name)
    assert marker not in str(missing.value)

    monkeypatch.setenv(env_name, "   ")
    with pytest.raises(RuntimeError, match="empty") as empty:
        read_required_seed_secret(env_name)
    assert marker not in str(empty.value)


def test_secret_file_missing_fails_without_echo(monkeypatch, tmp_path):
    env_name = "FRCAIXINHA_SEED_ADMIN_PASSWORD"
    marker = "secret-path-must-not-appear"
    missing_file = tmp_path / marker
    monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv(f"{env_name}_FILE", str(missing_file))

    with pytest.raises(RuntimeError) as exc_info:
        read_required_seed_secret(env_name)

    assert marker not in str(exc_info.value)


def test_secret_file_is_read_without_printing_value(monkeypatch, tmp_path, capsys):
    env_name = "FRCAIXINHA_HOMOLOGATION_PASSWORD"
    secret = "private-seed-test-secret"
    secret_file = tmp_path / "seed-secret"
    secret_file.write_text(f"{secret}\n", encoding="utf-8")
    monkeypatch.setenv(f"{env_name}_FILE", str(secret_file))
    monkeypatch.setenv(env_name, "ignored-environment-secret")

    assert read_required_seed_secret(env_name) == secret
    assert secret not in capsys.readouterr().out


def test_seed_gate_failure_happens_before_secret_loader_and_database(monkeypatch):
    monkeypatch.setattr(seed_safety.settings, "app_env", "production")
    loader_calls = []
    session_calls = []
    monkeypatch.setattr(
        standard_seed,
        "read_required_seed_secret",
        lambda _name: loader_calls.append(True),
    )
    monkeypatch.setattr(
        standard_seed,
        "SessionLocal",
        lambda: session_calls.append(True),
    )

    with pytest.raises(RuntimeError, match="forbidden in production") as exc_info:
        standard_seed.seed()

    assert loader_calls == []
    assert session_calls == []
    assert "DATABASE_URL" not in str(exc_info.value)
    assert "JWT_SECRET" not in str(exc_info.value)


@pytest.mark.parametrize("seed_name", ["standard", "homologation"])
def test_missing_seed_password_fails_before_sessionlocal(monkeypatch, seed_name):
    confirmation_env, confirmation, secret_env = CONFIRMATIONS[seed_name]
    monkeypatch.setattr(seed_safety.settings, "app_env", "development")
    monkeypatch.setenv(confirmation_env, confirmation)
    monkeypatch.delenv(secret_env, raising=False)
    monkeypatch.delenv(f"{secret_env}_FILE", raising=False)
    session_calls = []
    target = standard_seed if seed_name == "standard" else homologation_seed
    monkeypatch.setattr(target, "SessionLocal", lambda: session_calls.append(True))

    with pytest.raises(RuntimeError, match="not configured"):
        target.seed()

    assert session_calls == []


def test_authorized_homologation_writer_hashes_supplied_password(monkeypatch):
    secret = "not-persisted-plaintext"
    hashed_values = []

    class Query:
        def filter(self, *_args):
            return self

        def first(self):
            return None

    class FakeDB:
        next_id = 1

        def query(self, _model):
            return Query()

        def add(self, row):
            row.id = self.next_id
            self.next_id += 1

        def flush(self):
            pass

    fake_security = ModuleType("app.core.security")
    fake_security.hash_password = (
        lambda value: hashed_values.append(value) or "hashed-test-value"
    )
    monkeypatch.setitem(sys.modules, "app.core.security", fake_security)

    user, member = homologation_seed.get_or_create_user(
        FakeDB(), 1, "Synthetic", "seed@example.test", "synthetic-cpf",
        SimpleNamespace(), secret,
    )

    assert user.password_hash == "hashed-test-value"
    assert hashed_values == [secret]
    assert member is None
    assert secret not in user.password_hash


def test_seeds_have_no_hardcoded_password_argument_or_password_stdout():
    standard_tree = ast.parse((ROOT / "app" / "seed.py").read_text(encoding="utf-8"))
    homologation_tree = ast.parse(
        (ROOT / "app" / "seed_homologacao.py").read_text(encoding="utf-8")
    )

    for tree in (standard_tree, homologation_tree):
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "hash_password":
                    assert node.args and isinstance(node.args[0], ast.Name)
                    assert node.args[0].id == "password"

    assert not any(
        isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name) and target.id == "TEST_PASSWORD"
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
        )
        for node in ast.walk(homologation_tree)
    )

    for node in ast.walk(homologation_tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "print":
                assert all(
                    not isinstance(child, ast.Name) or child.id != "password"
                    for argument in node.args
                    for child in ast.walk(argument)
                )
                assert all(
                    not isinstance(argument, ast.Constant)
                    or not isinstance(argument.value, str)
                    or "password" not in argument.value.lower()
                    for argument in node.args
                )


def test_helper_does_not_log_or_include_secrets_in_failures(monkeypatch, caplog):
    monkeypatch.setattr(seed_safety.settings, "app_env", "production")
    marker = "private-marker-secret"
    monkeypatch.setenv("FRCAIXINHA_ENABLE_STANDARD_SEED", marker)
    with pytest.raises(RuntimeError) as exc_info:
        require_seed_execution(
            seed_name="standard",
            confirmation_env="FRCAIXINHA_ENABLE_STANDARD_SEED",
            allowed_non_production_envs=ALLOWED_ENVS,
        )
    assert marker not in str(exc_info.value)
    assert marker not in caplog.text
