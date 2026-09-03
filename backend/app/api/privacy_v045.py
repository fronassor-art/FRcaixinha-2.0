from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.deps import current_user, require_admin
from app.db.session import get_db
from app.models import User, PrivacyRequest, ConsentRecord, DataAccessLog, AuditLog
from app.services.privacy_v045 import export_user_data, request_privacy, record_consent, log_access, anonymize_user

router=APIRouter(prefix='/privacy',tags=['privacy'])
class ConsentIn(BaseModel):
    consent_type:str=Field(...,min_length=1,max_length=40); version:str=Field(...,min_length=1,max_length=30); granted:bool
class RequestIn(BaseModel):
    request_type:str=Field(...,pattern='^(ACCESS|ANONYMIZE)$'); reason:str|None=Field(None,max_length=1000)
class DecisionIn(BaseModel):
    approve:bool; note:str|None=Field(None,max_length=1000)

@router.get('/export')
def export(request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    data=export_user_data(db,user.id); log_access(db,user.id,user.id,'DATA_EXPORT','OWN_DATA',request.client.host if request.client else None,request.headers.get('user-agent')); db.commit(); return data

@router.post('/consents')
def consent(body:ConsentIn,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    row=record_consent(db,user.id,body.consent_type,body.version,body.granted,ip_address=request.client.host if request.client else None); db.commit(); return {'id':row.id,'status':'RECORDED'}

@router.post('/requests')
def privacy_request(body:RequestIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    row,created=request_privacy(db,user.id,body.request_type,body.reason); db.commit(); return {'id':row.id,'status':row.status,'created':created}

@router.get('/requests')
def my_requests(user:User=Depends(current_user),db:Session=Depends(get_db)):
    rows=db.query(PrivacyRequest).filter(PrivacyRequest.user_id==user.id).order_by(PrivacyRequest.requested_at.desc()).all(); return {'items':[{'id':r.id,'type':r.request_type,'status':r.status,'reason':r.reason,'requested_at':r.requested_at.isoformat(),'decided_at':r.decided_at.isoformat() if r.decided_at else None} for r in rows]}

@router.get('/admin/requests')
def admin_requests(status:str|None=Query(None),admin=Depends(require_admin),db:Session=Depends(get_db)):
    q=db.query(PrivacyRequest)
    if status:q=q.filter(PrivacyRequest.status==status.upper())
    rows=q.order_by(PrivacyRequest.requested_at.desc()).limit(500).all(); return {'items':[{'id':r.id,'user_id':r.user_id,'type':r.request_type,'status':r.status,'reason':r.reason,'requested_at':r.requested_at.isoformat(),'decided_at':r.decided_at.isoformat() if r.decided_at else None} for r in rows]}

@router.post('/admin/requests/{request_id}/decision')
def decision(request_id:int,body:DecisionIn,admin=Depends(require_admin),db:Session=Depends(get_db)):
    row=db.get(PrivacyRequest,request_id)
    if not row: raise HTTPException(404,'Solicitação não encontrada.')
    if row.status!='REQUESTED': raise HTTPException(409,'Solicitação já decidida.')
    if body.approve and row.request_type=='ANONYMIZE': anonymize_user(db,row.user_id,admin.id)
    row.status='APPROVED' if body.approve else 'REJECTED'; row.decided_by=admin.id; row.decision_note=body.note; row.decided_at=datetime.now(timezone.utc)
    db.add(AuditLog(actor_user_id=admin.id,action='PRIVACY_REQUEST_DECISION',entity_type='PRIVACY_REQUEST',entity_id=str(row.id),details=f'{row.request_type}:{row.status}'))
    db.commit(); return {'id':row.id,'status':row.status}

@router.get('/admin/access-log')
def access_log(limit:int=Query(100,ge=1,le=500),admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=db.query(DataAccessLog).order_by(DataAccessLog.created_at.desc()).limit(limit).all(); return {'items':[{'id':r.id,'actor_user_id':r.actor_user_id,'subject_user_id':r.subject_user_id,'action':r.action,'resource':r.resource,'ip_address':r.ip_address,'created_at':r.created_at.isoformat()} for r in rows]}
