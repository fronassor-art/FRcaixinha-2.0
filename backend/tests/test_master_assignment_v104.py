import json

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import AuditLog, User
from app.ops.assign_master import MasterAssignmentError, assign_initial_master, main

from test_payment_settlement_v103 import _db


def _user(db, suffix, *, role="USER", active=True, master=False):
    row = User(name=f"User {suffix}", email=f"{suffix}@test", cpf=f"cpf-{suffix}", password_hash="not-a-real-password", role=role, is_active=active, is_master=master)
    db.add(row)
    db.flush()
    return row


def _assign(db, target, operator, *, reason="bootstrap inicial", confirm=True):
    return assign_initial_master(db, target.id, operator.id, reason, confirm)


def test_assigns_explicit_active_admin_and_writes_one_audit_log():
    db = _db(); target = _user(db, "target", role="ADMIN"); operator = _user(db, "operator", role="ADMIN"); db.commit()
    result = _assign(db, target, operator)
    audit = db.query(AuditLog).one()
    assert result.id == target.id and db.get(User, target.id).is_master is True
    assert (audit.action, audit.entity_type, audit.entity_id, audit.actor_user_id) == ("MASTER_ASSIGNED_INITIAL", "USER", str(target.id), operator.id)
    assert json.loads(audit.details) == {"operator_user_id": operator.id, "reason": "bootstrap inicial", "target_user_id": target.id}
    db.close()


@pytest.mark.parametrize("reason", ["", "   ", None])
def test_assignment_requires_non_empty_reason(reason):
    db = _db(); target = _user(db, "target", role="ADMIN"); operator = _user(db, "operator", role="ADMIN"); db.commit()
    with pytest.raises(MasterAssignmentError, match="motivo"):
        _assign(db, target, operator, reason=reason)
    assert db.query(AuditLog).count() == 0 and db.get(User, target.id).is_master is False
    db.close()


def test_assignment_requires_explicit_confirmation():
    db = _db(); target = _user(db, "target", role="ADMIN"); operator = _user(db, "operator", role="ADMIN"); db.commit()
    with pytest.raises(MasterAssignmentError, match="confirm"):
        _assign(db, target, operator, confirm=False)
    assert db.query(AuditLog).count() == 0
    db.close()


def test_assignment_rejects_missing_target():
    db = _db(); operator = _user(db, "operator", role="ADMIN"); db.commit()
    with pytest.raises(MasterAssignmentError, match="alvo"):
        assign_initial_master(db, 999999, operator.id, "motivo", True)
    db.close()


@pytest.mark.parametrize("role,active,message", [("USER", True, "ADMIN"), ("ADMIN", False, "ativo")])
def test_assignment_rejects_invalid_target(role, active, message):
    db = _db(); target = _user(db, "target", role=role, active=active); operator = _user(db, "operator", role="ADMIN"); db.commit()
    with pytest.raises(MasterAssignmentError, match=message):
        _assign(db, target, operator)
    assert db.query(AuditLog).count() == 0
    db.close()


def test_assignment_rejects_missing_operator():
    db = _db(); target = _user(db, "target", role="ADMIN"); db.commit()
    with pytest.raises(MasterAssignmentError, match="operador"):
        assign_initial_master(db, target.id, 999999, "motivo", True)
    db.close()


@pytest.mark.parametrize("role,active,message", [("USER", True, "ADMIN"), ("ADMIN", False, "ativo")])
def test_assignment_rejects_invalid_operator(role, active, message):
    db = _db(); target = _user(db, "target", role="ADMIN"); operator = _user(db, "operator", role=role, active=active); db.commit()
    with pytest.raises(MasterAssignmentError, match=message):
        _assign(db, target, operator)
    db.close()


def test_existing_other_master_blocks_assignment():
    db = _db(); existing = _user(db, "existing", role="ADMIN", master=True); target = _user(db, "target", role="ADMIN"); operator = _user(db, "operator", role="ADMIN"); db.commit()
    with pytest.raises(MasterAssignmentError, match="já existe"):
        _assign(db, target, operator)
    assert db.get(User, target.id).is_master is False and db.query(AuditLog).count() == 0 and db.get(User, existing.id).is_master is True
    db.close()


def test_repeating_same_master_is_idempotent_without_second_audit():
    db = _db(); target = _user(db, "target", role="ADMIN"); operator = _user(db, "operator", role="ADMIN"); db.commit()
    _assign(db, target, operator); result = _assign(db, target, operator, reason="segunda tentativa")
    assert result.id == target.id and db.query(AuditLog).count() == 1
    db.close()


def test_commit_failure_rolls_back_promotion_and_audit(monkeypatch):
    db = _db(); target = _user(db, "target", role="ADMIN"); operator = _user(db, "operator", role="ADMIN"); db.commit()
    def fail_commit():
        raise IntegrityError("forced conflict", {}, Exception("conflict"))
    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(MasterAssignmentError, match="conflito"):
        _assign(db, target, operator)
    assert db.get(User, target.id).is_master is False and db.query(AuditLog).count() == 0
    db.close()


def test_cli_requires_confirmation():
    with pytest.raises(SystemExit):
        main(["--target-user-id", "1", "--operator-user-id", "2", "--reason", "motivo"])


def test_cli_rejects_empty_reason():
    with pytest.raises(SystemExit):
        main(["--target-user-id", "1", "--operator-user-id", "2", "--reason", "", "--confirm"])
