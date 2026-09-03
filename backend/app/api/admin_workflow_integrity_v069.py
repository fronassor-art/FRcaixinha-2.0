from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.services.workflow_evidence_integrity_v069 import verify_file, verify_all, verify_chain

router=APIRouter(prefix='/admin/workflow-integrity',tags=['workflow-evidence-integrity-v069'])
@router.post('/files/{file_id}/verify')
def verify(file_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: row=verify_file(db,file_id,admin.id); db.commit(); return {'id':row.id,'status':row.status,'expected_sha256':row.expected_sha256,'observed_sha256':row.observed_sha256,'event_hash':row.event_hash}
    except ValueError as e: db.rollback(); raise HTTPException(404,str(e))
@router.post('/verify-all')
def verify_all_files(admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: result=verify_all(db,admin.id); db.commit(); return result
    except Exception: db.rollback(); raise
@router.get('/chain')
def chain(admin=Depends(require_admin),db:Session=Depends(get_db)): return verify_chain(db)
