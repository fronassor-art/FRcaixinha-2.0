from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path
from datetime import datetime, timezone

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    AuditLog,
    OperationalWorkflowOrchestration,
    OperationalWorkflowTask,
    WorkflowExecutionEvidence,
    WorkflowExecutionEvidenceFile,
)

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".txt", ".csv", ".zip"}
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def utcnow():
    return datetime.now(timezone.utc)


def _allowed_types() -> set[str]:
    return {x.strip().lower() for x in settings.workflow_evidence_allowed_types.split(",") if x.strip()}


def sanitize_filename(name: str | None) -> str:
    raw = (name or "arquivo").replace("\\", "/").split("/")[-1]
    raw = SAFE_NAME_RE.sub("_", raw).strip("._")
    return raw[:255] or "arquivo"


def validate_upload_metadata(filename: str | None, content_type: str | None) -> tuple[str, str]:
    safe = sanitize_filename(filename)
    ext = Path(safe).suffix.lower()
    ctype = (content_type or "application/octet-stream").lower().split(";")[0].strip()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("Extensão de arquivo não permitida.")
    if ctype not in _allowed_types():
        raise ValueError("Tipo MIME não permitido.")
    return safe, ctype


def _storage_path(storage_key: str) -> Path:
    root = Path(settings.workflow_evidence_storage_root).expanduser().resolve()
    candidate = (root / storage_key).resolve()
    if root != candidate.parent and root not in candidate.parents:
        raise ValueError("Chave de armazenamento inválida.")
    return candidate


def _audit(db: Session, actor_id: int, action: str, task_id: int, details: str):
    db.add(AuditLog(actor_user_id=actor_id, action=action, entity_type="OperationalWorkflowTask", entity_id=str(task_id), details=details))


def _next_version(db: Session, evidence_id: int) -> int:
    latest = (
        db.query(WorkflowExecutionEvidenceFile)
        .filter_by(evidence_id=evidence_id)
        .order_by(WorkflowExecutionEvidenceFile.version.desc())
        .first()
    )
    return (latest.version + 1) if latest else 1


def upload_file(db: Session, task: OperationalWorkflowTask, evidence: WorkflowExecutionEvidence, actor_id: int, upload: UploadFile):
    orch = db.query(OperationalWorkflowOrchestration).filter_by(task_id=task.id).first()
    if not orch or orch.execution_state != "IN_EXECUTION" or orch.started_by != actor_id:
        raise ValueError("Somente o administrador que iniciou a execução pode anexar arquivos.")
    if evidence.task_id != task.id:
        raise ValueError("A evidência não pertence à tarefa informada.")
    if evidence.evidence_type != "ATTACHMENT":
        raise ValueError("A evidência precisa ser do tipo ATTACHMENT.")

    safe_name, ctype = validate_upload_metadata(upload.filename, upload.content_type)
    max_bytes = int(settings.workflow_evidence_max_bytes)
    if max_bytes <= 0:
        raise ValueError("Limite de tamanho de arquivo inválido.")

    storage_key = f"{task.id}/{uuid.uuid4().hex}.bin"
    path = _storage_path(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    digest = hashlib.sha256()
    try:
        with path.open("wb") as out:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError(f"Arquivo excede o limite de {max_bytes} bytes.")
                digest.update(chunk)
                out.write(chunk)
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    now = utcnow()
    row = WorkflowExecutionEvidenceFile(
        evidence_id=evidence.id,
        version=_next_version(db, evidence.id),
        original_name=safe_name,
        storage_key=storage_key,
        content_type=ctype,
        size_bytes=size,
        sha256=digest.hexdigest(),
        uploaded_by=actor_id,
        created_at=now,
    )
    db.add(row)
    db.flush()
    _audit(db, actor_id, "WORKFLOW_EVIDENCE_FILE_UPLOADED", task.id, f"file_id={row.id};version={row.version};bytes={size};sha256={row.sha256}")
    return row


def get_file_for_download(db: Session, file_id: int):
    row = db.get(WorkflowExecutionEvidenceFile, file_id)
    if not row or row.revoked_at:
        raise ValueError("Arquivo não encontrado ou revogado.")
    path = _storage_path(row.storage_key)
    if not path.is_file():
        raise ValueError("Arquivo físico não encontrado.")
    return row, path


def list_files(db: Session, task_id: int):
    rows = (
        db.query(WorkflowExecutionEvidenceFile)
        .join(WorkflowExecutionEvidence, WorkflowExecutionEvidence.id == WorkflowExecutionEvidenceFile.evidence_id)
        .filter(WorkflowExecutionEvidence.task_id == task_id)
        .order_by(WorkflowExecutionEvidenceFile.created_at.asc())
        .all()
    )
    return [
        {"id": r.id, "evidence_id": r.evidence_id, "version": r.version, "original_name": r.original_name,
         "content_type": r.content_type, "size_bytes": r.size_bytes, "sha256": r.sha256,
         "uploaded_by": r.uploaded_by, "created_at": r.created_at.isoformat(), "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None}
        for r in rows
    ]
