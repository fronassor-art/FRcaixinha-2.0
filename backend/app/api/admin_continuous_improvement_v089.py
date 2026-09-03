from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import ContinuousImprovementExecution, AuditLog
from app.services.continuous_improvement_evidence_v089 import upload_file, list_files, get_file, verify_execution_evidence, verify_chain
router=APIRouter(prefix='/admin/continuous-improvement-evidence',tags=['continuous-improvement-v089'])
@router.post('/executions/{execution_id}/files')
def upload(execution_id:int,file:UploadFile=File(...),admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: row=upload_file(db,execution_id,admin.id,file); db.commit()
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
    finally: file.file.close()
    return {'id':row.id,'execution_id':row.execution_id,'version':row.version,'original_name':row.original_name,'content_type':row.content_type,'size_bytes':row.size_bytes,'sha256':row.sha256}
@router.get('/executions/{execution_id}/files')
def files(execution_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    if not db.get(ContinuousImprovementExecution,execution_id): raise HTTPException(404,'execution_not_found')
    return {'execution_id':execution_id,'items':list_files(db,execution_id)}
@router.get('/files/{file_id}')
def download(file_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: row,path=get_file(db,file_id)
    except ValueError as e: raise HTTPException(404,str(e))
    db.add(AuditLog(actor_user_id=admin.id,action='IMPROVEMENT_EXECUTION_EVIDENCE_ACCESSED',entity_type='ContinuousImprovementExecutionEvidenceFile',entity_id=str(row.id),details=f'sha256={row.sha256}')); db.commit()
    return FileResponse(path,media_type=row.content_type,filename=row.original_name,headers={'X-Content-SHA256':row.sha256})
@router.post('/executions/{execution_id}/verify-integrity')
def verify(execution_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: result=verify_execution_evidence(db,execution_id,admin.id); db.commit()
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
    if not result['valid']: raise HTTPException(409,detail=result)
    return result
@router.get('/integrity/chain')
def chain(admin=Depends(require_admin),db:Session=Depends(get_db)): return verify_chain(db)
