from __future__ import annotations
import hashlib, json
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.models import Loan, SecureReleaseAuthorization, AuditLog
from app.services.integrated_governance_v058 import evaluate_integrated
from app.services.loan_engine_v17 import release_loan

FRESHNESS_MINUTES = 15

def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)

def sha(data):
    return hashlib.sha256(canonical(data).encode()).hexdigest()

def _fresh(result):
    return result.get("final_decision") == "ALLOW" and result.get("release_ready") is True

def create_authorization(db: Session, *, loan_id: int, actor_id: int, horizon_months: int = 12, scenario: str = "BASE"):
    loan = db.get(Loan, loan_id)
    if not loan:
        raise ValueError("Empréstimo não encontrado.")
    if loan.status != "APPROVED":
        raise ValueError("Somente empréstimos aprovados podem entrar na liberação segura.")
    active = db.query(SecureReleaseAuthorization).filter(
        SecureReleaseAuthorization.loan_id == loan_id,
        SecureReleaseAuthorization.status.in_(["AUTHORIZED", "CONFIRMED"]),
    ).first()
    if active:
        raise ValueError("Já existe uma autorização de liberação em andamento para este empréstimo.")
    result = evaluate_integrated(db, loan_id, horizon_months=horizon_months, scenario=scenario)
    if not _fresh(result):
        raise ValueError({"code": "GOVERNANCE_BLOCKED", "governance": result})
    expires = datetime.now(timezone.utc) + timedelta(minutes=FRESHNESS_MINUTES)
    row = SecureReleaseAuthorization(
        loan_id=loan_id, group_id=result["group_id"], governance_hash=result["integrity_hash"],
        governance_snapshot=json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str),
        status="AUTHORIZED", authorized_by=actor_id, authorized_at=datetime.now(timezone.utc),
        expires_at=expires, confirmation_count=1,
    )
    db.add(row)
    db.add(AuditLog(actor_user_id=actor_id, action="SECURE_RELEASE_AUTHORIZED", entity_type="LOAN", entity_id=str(loan_id), details=canonical({"governance_hash": result["integrity_hash"], "expires_at": expires.isoformat()})))
    db.flush()
    return row, result

def confirm_and_release(db: Session, *, authorization_id: int, actor_id: int):
    row = db.query(SecureReleaseAuthorization).filter(SecureReleaseAuthorization.id == authorization_id).with_for_update().first()
    if not row:
        raise ValueError("Autorização de liberação não encontrada.")
    if row.status != "AUTHORIZED":
        raise ValueError("Autorização não está aguardando segunda confirmação.")
    if row.authorized_by == actor_id:
        raise ValueError("A segunda confirmação deve ser feita por um administrador diferente.")
    now = datetime.now(timezone.utc)
    if row.expires_at <= now:
        row.status = "EXPIRED"
        db.add(AuditLog(actor_user_id=actor_id, action="SECURE_RELEASE_EXPIRED", entity_type="LOAN", entity_id=str(row.loan_id), details="governance authorization expired"))
        raise ValueError({"code": "AUTHORIZATION_EXPIRED"})
    current = evaluate_integrated(db, row.loan_id, horizon_months=12, scenario="BASE")
    if not _fresh(current) or current["integrity_hash"] != row.governance_hash:
        row.status = "STALE"
        db.add(AuditLog(actor_user_id=actor_id, action="SECURE_RELEASE_STALE", entity_type="LOAN", entity_id=str(row.loan_id), details=canonical({"authorized_hash": row.governance_hash, "current_hash": current.get("integrity_hash")})))
        raise ValueError({"code": "GOVERNANCE_STALE", "current": current})
    row.confirmed_by = actor_id
    row.confirmed_at = now
    row.confirmation_count = 2
    row.status = "CONFIRMED"
    loan = db.get(Loan, row.loan_id)
    if not loan:
        raise ValueError("Empréstimo não encontrado.")
    release_loan(db, loan, actor_id)
    row.status = "EXECUTED"
    row.executed_at = datetime.now(timezone.utc)
    row.execution_hash = sha({"authorization_id": row.id, "loan_id": row.loan_id, "governance_hash": row.governance_hash, "authorized_by": row.authorized_by, "confirmed_by": actor_id, "status": row.status})
    db.add(AuditLog(actor_user_id=actor_id, action="SECURE_RELEASE_EXECUTED", entity_type="LOAN", entity_id=str(row.loan_id), details=canonical({"authorization_id": row.id, "execution_hash": row.execution_hash})))
    return row, loan, current
