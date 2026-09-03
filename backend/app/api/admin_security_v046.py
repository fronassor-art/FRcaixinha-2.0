from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import SecurityEvent, TrustedDevice
router=APIRouter(prefix='/admin/security',tags=['security'])
@router.get('/events')
def events(limit:int=Query(100,ge=1,le=500),admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=db.query(SecurityEvent).order_by(SecurityEvent.created_at.desc()).limit(limit).all()
    return {'items':[{'id':r.id,'user_id':r.user_id,'type':r.event_type,'severity':r.severity,'ip_address':r.ip_address,'created_at':r.created_at.isoformat(),'details':r.details} for r in rows]}
@router.get('/summary')
def summary(admin=Depends(require_admin),db:Session=Depends(get_db)):
    from datetime import datetime,timedelta,timezone
    since=datetime.now(timezone.utc)-timedelta(hours=24)
    rows=db.query(SecurityEvent).filter(SecurityEvent.created_at>=since).all()
    return {'last_24h':len(rows),'critical':sum(r.severity=='CRITICAL' for r in rows),'warning':sum(r.severity=='WARNING' for r in rows),'failed_2fa':sum(r.event_type=='LOGIN_2FA_FAILED' for r in rows)}
