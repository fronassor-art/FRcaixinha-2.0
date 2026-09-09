from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import ContinuousImprovementCertification
from app.services.continuous_improvement_certification_v090 import certify, verify_certificate
import json
router=APIRouter(prefix='/admin/continuous-improvement-certification',tags=['continuous-improvement-v090'])
class CertifyIn(BaseModel): note:str=Field(min_length=3,max_length=4000)
@router.post('/executions/{execution_id}/certify')
def create(execution_id:int,body:CertifyIn,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try: row=certify(db,execution_id,admin.id,body.note); db.commit()
    except ValueError as e: db.rollback(); raise HTTPException(400,str(e))
    return {'id':row.id,'certificate_id':row.certificate_id,'status':row.status,'package_hash':row.package_hash,'certified_at':row.certified_at.isoformat()}
@router.get('')
def listing(status:str|None=None,limit:int=Query(50,ge=1,le=200),admin=Depends(require_admin),db:Session=Depends(get_db)):
    q=db.query(ContinuousImprovementCertification).order_by(ContinuousImprovementCertification.id.desc())
    if status:q=q.filter_by(status=status)
    rows=q.limit(limit).all()
    return {'items':[{'id':r.id,'execution_id':r.execution_id,'certificate_id':r.certificate_id,'status':r.status,'package_hash':r.package_hash,'certified_by':r.certified_by,'certified_at':r.certified_at.isoformat()} for r in rows]}
@router.get('/{certificate_id}/verify')
def verify(certificate_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    try:return verify_certificate(db,certificate_id)
    except ValueError as e:raise HTTPException(404,str(e))
@router.get('/{certificate_id}')
def detail(certificate_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    r=db.get(ContinuousImprovementCertification,certificate_id)
    if not r: raise HTTPException(404,'certificate_not_found')
    return {'id':r.id,'execution_id':r.execution_id,'certificate_id':r.certificate_id,'status':r.status,'package_hash':r.package_hash,'certified_by':r.certified_by,'certified_at':r.certified_at.isoformat(),'certification_note':r.certification_note,'package':json.loads(r.package_json)}
