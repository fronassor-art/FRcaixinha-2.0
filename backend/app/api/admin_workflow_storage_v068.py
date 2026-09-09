from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db
from app.models import OperationalWorkflowTask, WorkflowExecutionEvidence
from app.services.workflow_evidence_storage_v068 import upload_file, get_file_for_download, list_files
from app.models import AuditLog

router = APIRouter(prefix="/admin/workflow-storage", tags=["workflow-evidence-storage-v068"])


def task_or_404(db, task_id):
    task = db.get(OperationalWorkflowTask, task_id)
    if not task:
        raise HTTPException(404, "Tarefa não encontrada.")
    return task


@router.post("/tasks/{task_id}/evidence/{evidence_id}/file")
def upload_attachment(task_id: int, evidence_id: int, file: UploadFile = File(...), admin=Depends(require_admin), db: Session = Depends(get_db)):
    task = task_or_404(db, task_id)
    evidence = db.get(WorkflowExecutionEvidence, evidence_id)
    if not evidence:
        raise HTTPException(404, "Evidência não encontrada.")
    try:
        row = upload_file(db, task, evidence, admin.id, file)
        db.commit()
        return {"id": row.id, "evidence_id": row.evidence_id, "version": row.version, "original_name": row.original_name,
                "content_type": row.content_type, "size_bytes": row.size_bytes, "sha256": row.sha256}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    finally:
        file.file.close()


@router.get("/tasks/{task_id}/files")
def files(task_id: int, admin=Depends(require_admin), db: Session = Depends(get_db)):
    task_or_404(db, task_id)
    return {"task_id": task_id, "items": list_files(db, task_id)}


@router.get("/files/{file_id}")
def download_file(file_id: int, admin=Depends(require_admin), db: Session = Depends(get_db)):
    try:
        row, path = get_file_for_download(db, file_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    db.add(AuditLog(actor_user_id=admin.id, action="WORKFLOW_EVIDENCE_FILE_ACCESSED", entity_type="WorkflowExecutionEvidenceFile", entity_id=str(row.id), details=f"sha256={row.sha256}"))
    db.commit()
    return FileResponse(path, media_type=row.content_type, filename=row.original_name, headers={"X-Content-SHA256": row.sha256})
