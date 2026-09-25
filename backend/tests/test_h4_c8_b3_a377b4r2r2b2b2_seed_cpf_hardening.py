import ast
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from app import seed as standard_seed
from app import seed_homologacao as homologation_seed
from app.core.cpf import CPFValidationError, normalize_cpf
from app.core import seed_safety
from app.core.seed_safety import read_required_seed_cpf
from app.models import Group, Member, User


ROOT = Path(__file__).resolve().parents[1]
VALID_CPFS = (
    "52998224725",
    "11144477735",
    "12345678909",
    "22233344405",
    "98765432100",
)
HOMO_CPF_ENVS = tuple(f"FRCAIXINHA_HOMOLOGATION_CPF_{index}" for index in range(1, 6))
STANDARD_CONFIRMATION = "I_UNDERSTAND_THIS_CREATES_AN_ADMIN_ACCOUNT"
HOMO_CONFIRMATION = "I_UNDERSTAND_THIS_WRITES_FAKE_FINANCIAL_DATA"


class SessionReached(RuntimeError):
    pass


class FakeQuery:
    def __init__(self, db, model):
        self.db = db
        self.model = model
        self.lookup = None

    def filter(self, expression):
        self.lookup = (self.model, expression.left.key, expression.right.value)
        return self

    def first(self):
        return self.db.rows.get(self.lookup)


class FakeDB:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.added = []
        self.committed = False

    def query(self, model):
        return FakeQuery(self, model)

    def add(self, row):
        self.added.append(row)

    def flush(self):
        pass

    def commit(self):
        self.committed = True

    def close(self):
        pass


@pytest.fixture(autouse=True)
def valid_test_cpfs():
    for cpf in VALID_CPFS:
        assert normalize_cpf(cpf) == cpf


def _authorize(monkeypatch, *, homologation=False):
    monkeypatch.setattr(seed_safety.settings, "app_env", "development")
    if homologation:
        monkeypatch.setenv("FRCAIXINHA_ENABLE_HOMOLOGATION_SEED", HOMO_CONFIRMATION)
        monkeypatch.setenv("FRCAIXINHA_HOMOLOGATION_PASSWORD", "seed-test-password")
    else:
        monkeypatch.setenv("FRCAIXINHA_ENABLE_STANDARD_SEED", STANDARD_CONFIRMATION)
        monkeypatch.setenv("FRCAIXINHA_SEED_ADMIN_PASSWORD", "seed-test-password")


def _fail_if_db_opens(monkeypatch, target):
    calls = []

    def fail():
        calls.append(True)
        raise SessionReached("database session reached")

    monkeypatch.setattr(target, "SessionLocal", fail)
    return calls


def test_required_seed_cpf_is_canonical_and_does_not_echo_invalid_value(monkeypatch, capsys):
    monkeypatch.setenv("TEST_SEED_CPF", "  529.982.247-25  ")
    assert read_required_seed_cpf("TEST_SEED_CPF") == VALID_CPFS[0]

    marker = "12345678900"
    monkeypatch.setenv("TEST_SEED_CPF", marker)
    with pytest.raises(RuntimeError) as caught:
        read_required_seed_cpf("TEST_SEED_CPF")
    assert marker not in str(caught.value)
    assert marker not in capsys.readouterr().out


@pytest.mark.parametrize("value", [None, "", "   "])
def test_standard_missing_cpf_fails_before_database(monkeypatch, value):
    _authorize(monkeypatch)
    if value is None:
        monkeypatch.delenv("FRCAIXINHA_SEED_ADMIN_CPF", raising=False)
    else:
        monkeypatch.setenv("FRCAIXINHA_SEED_ADMIN_CPF", value)
    calls = _fail_if_db_opens(monkeypatch, standard_seed)

    with pytest.raises(RuntimeError, match="Required seed CPF"):
        standard_seed.seed()
    assert calls == []


def test_standard_formatted_cpf_reaches_database_only_canonical(monkeypatch):
    _authorize(monkeypatch)
    monkeypatch.setenv("FRCAIXINHA_SEED_ADMIN_CPF", "529.982.247-25")
    calls = _fail_if_db_opens(monkeypatch, standard_seed)
    with pytest.raises(SessionReached):
        standard_seed.seed()
    assert calls == [True]


@pytest.mark.parametrize("invalid", ["12345678900", "cpf-not-a-number"])
def test_standard_invalid_cpf_fails_before_database_without_echo(monkeypatch, invalid):
    _authorize(monkeypatch)
    monkeypatch.setenv("FRCAIXINHA_SEED_ADMIN_CPF", invalid)
    calls = _fail_if_db_opens(monkeypatch, standard_seed)
    with pytest.raises(RuntimeError) as caught:
        standard_seed.seed()
    assert calls == []
    assert invalid not in str(caught.value)


@pytest.mark.parametrize("missing_env", HOMO_CPF_ENVS)
def test_each_missing_homologation_cpf_fails_before_database(monkeypatch, missing_env):
    _authorize(monkeypatch, homologation=True)
    for env_name, cpf in zip(HOMO_CPF_ENVS, VALID_CPFS):
        monkeypatch.setenv(env_name, cpf)
    monkeypatch.delenv(missing_env)
    calls = _fail_if_db_opens(monkeypatch, homologation_seed)

    with pytest.raises(RuntimeError, match="Required seed CPF"):
        homologation_seed.seed()
    assert calls == []


@pytest.mark.parametrize("invalid", ["12345678900", "invalid-cpf"])
def test_invalid_homologation_cpf_fails_before_database(monkeypatch, invalid):
    _authorize(monkeypatch, homologation=True)
    for env_name, cpf in zip(HOMO_CPF_ENVS, VALID_CPFS):
        monkeypatch.setenv(env_name, cpf)
    monkeypatch.setenv(HOMO_CPF_ENVS[2], invalid)
    calls = _fail_if_db_opens(monkeypatch, homologation_seed)

    with pytest.raises(RuntimeError) as caught:
        homologation_seed.seed()
    assert calls == []
    assert invalid not in str(caught.value)


def test_homologation_requires_five_distinct_canonical_cpfs_before_database(monkeypatch):
    _authorize(monkeypatch, homologation=True)
    values = list(VALID_CPFS)
    values[0] = "529.982.247-25"
    for env_name, cpf in zip(HOMO_CPF_ENVS, values):
        monkeypatch.setenv(env_name, cpf)
    calls = _fail_if_db_opens(monkeypatch, homologation_seed)
    with pytest.raises(SessionReached):
        homologation_seed.seed()
    assert calls == [True]

    monkeypatch.setenv(HOMO_CPF_ENVS[4], "52998224725")
    calls.clear()
    with pytest.raises(RuntimeError, match="must be distinct"):
        homologation_seed.seed()
    assert calls == []


def test_seed_sources_no_longer_contain_invalid_hardcoded_cpfs():
    standard_source = (ROOT / "app" / "seed.py").read_text(encoding="utf-8")
    homologation_source = (ROOT / "app" / "seed_homologacao.py").read_text(encoding="utf-8")
    assert "00000000000" not in standard_source
    for suffix in range(1, 6):
        assert f"9000000000{suffix}" not in homologation_source


def test_standard_admin_identity_is_reused_only_for_exact_valid_cpf(monkeypatch):
    existing = SimpleNamespace(cpf=VALID_CPFS[0], password_hash="unchanged")
    db = FakeDB({(User, "email", "admin@frcaixinha.com"): existing})
    assert standard_seed.get_or_create_admin(db, VALID_CPFS[0], "unused") is existing
    assert existing.cpf == VALID_CPFS[0]
    assert db.added == []

    with pytest.raises(RuntimeError, match="conflicts"):
        standard_seed.get_or_create_admin(db, VALID_CPFS[1], "unused")
    assert existing.cpf == VALID_CPFS[0]


def test_new_standard_admin_persists_only_canonical_cpf(monkeypatch):
    hashed_values = []
    fake_security = ModuleType("app.core.security")
    fake_security.hash_password = lambda value: hashed_values.append(value) or "hashed-value"
    monkeypatch.setitem(sys.modules, "app.core.security", fake_security)
    db = FakeDB()

    admin = standard_seed.get_or_create_admin(db, "529.982.247-25", "seed-password")
    assert admin.cpf == VALID_CPFS[0]
    assert admin.role == "ADMIN"
    assert not getattr(admin, "is_master", False)
    assert admin.password_hash == "hashed-value"
    assert hashed_values == ["seed-password"]
    assert db.added == [admin]


@pytest.mark.parametrize("legacy_cpf", ["00000000000", "not-a-cpf"])
def test_standard_invalid_existing_admin_identity_fails_closed(legacy_cpf):
    existing = SimpleNamespace(cpf=legacy_cpf)
    db = FakeDB({(User, "email", "admin@frcaixinha.com"): existing})
    with pytest.raises(RuntimeError, match="identity is invalid") as caught:
        standard_seed.get_or_create_admin(db, VALID_CPFS[0], "unused")
    assert legacy_cpf not in str(caught.value)
    assert existing.cpf == legacy_cpf
    assert db.added == []


def test_standard_cpf_owned_by_other_email_fails_closed():
    owner = SimpleNamespace(cpf=VALID_CPFS[0])
    db = FakeDB({(User, "cpf", VALID_CPFS[0]): owner})
    with pytest.raises(RuntimeError, match="belongs to another user"):
        standard_seed.get_or_create_admin(db, VALID_CPFS[0], "unused")
    assert db.added == []


def test_homologation_existing_identity_requires_exact_cpf_and_never_rewrites():
    existing = SimpleNamespace(id=12, cpf=VALID_CPFS[0], password_hash="unchanged")
    db = FakeDB({(User, "email", "member@example.test"): existing})
    user, member = homologation_seed.get_or_create_user(
        db, 1, "Synthetic", "member@example.test", VALID_CPFS[0], 5000, "unused"
    )
    assert user is existing
    assert member is None
    assert existing.cpf == VALID_CPFS[0]
    assert db.added == []

    with pytest.raises(RuntimeError, match="conflicts"):
        homologation_seed.get_or_create_user(
            db, 1, "Synthetic", "member@example.test", VALID_CPFS[1], 5000, "unused"
        )
    assert existing.cpf == VALID_CPFS[0]


def test_homologation_invalid_legacy_and_other_email_owner_fail_closed():
    legacy = SimpleNamespace(id=12, cpf="90000000001")
    db = FakeDB({(User, "email", "member@example.test"): legacy})
    with pytest.raises(RuntimeError, match="identity is invalid") as caught:
        homologation_seed.get_or_create_user(
            db, 1, "Synthetic", "member@example.test", VALID_CPFS[0], 5000, "unused"
        )
    assert "90000000001" not in str(caught.value)
    assert legacy.cpf == "90000000001"
    assert db.added == []

    other = SimpleNamespace(id=33, cpf=VALID_CPFS[0])
    db = FakeDB({(User, "cpf", VALID_CPFS[0]): other})
    with pytest.raises(RuntimeError, match="belongs to another user"):
        homologation_seed.get_or_create_user(
            db, 1, "Synthetic", "new@example.test", VALID_CPFS[0], 5000, "unused"
        )
    assert db.added == []


def test_seed_cpf_wiring_keeps_existing_production_guards_and_no_financial_edits():
    standard_tree = ast.parse((ROOT / "app" / "seed.py").read_text(encoding="utf-8"))
    homologation_tree = ast.parse(
        (ROOT / "app" / "seed_homologacao.py").read_text(encoding="utf-8")
    )
    for tree in (standard_tree, homologation_tree):
        seed_fn = next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "seed"
        )
        calls = [
            node.func.id
            for node in ast.walk(seed_fn)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        assert calls.index("require_seed_execution") < calls.index("SessionLocal")
    assert "hash_password" in (ROOT / "app" / "seed.py").read_text(encoding="utf-8")
    assert "calculate_linear_amortization" in (ROOT / "app" / "seed_homologacao.py").read_text(encoding="utf-8")


def test_no_cpf_is_written_to_stdout_or_exceptions(monkeypatch, capsys):
    marker = "12345678900"
    monkeypatch.setenv("TEST_SEED_CPF", marker)
    with pytest.raises(RuntimeError) as caught:
        read_required_seed_cpf("TEST_SEED_CPF")
    output = capsys.readouterr().out
    assert marker not in str(caught.value)
    assert marker not in output


def test_authorized_homologation_writer_still_hashes_external_password(monkeypatch):
    secret = "seed-password-test-value"
    hashed_values = []
    fake_security = ModuleType("app.core.security")
    fake_security.hash_password = lambda value: hashed_values.append(value) or "hashed-value"
    monkeypatch.setitem(sys.modules, "app.core.security", fake_security)
    db = FakeDB()

    user, member = homologation_seed.get_or_create_user(
        db, 1, "Synthetic", "seed@example.test", VALID_CPFS[0], 5000, secret
    )
    assert user.cpf == VALID_CPFS[0]
    assert user.password_hash == "hashed-value"
    assert hashed_values == [secret]
    assert secret not in user.password_hash
    assert member is None
