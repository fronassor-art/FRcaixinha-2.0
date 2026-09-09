from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.api.deps import current_user
from app.db.session import get_db
from app.models import User
from app.services.member_portal_v043 import portal_dashboard
from app.services.privacy_v045 import log_access

router = APIRouter(prefix='/member-portal', tags=['member-portal'])

@router.get('/dashboard')
def dashboard(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    result = portal_dashboard(db, user.id)
    log_access(db, user.id, user.id, 'PORTAL_VIEW', 'MEMBER_PORTAL', request.client.host if request.client else None, request.headers.get('user-agent'))
    db.commit()
    if not result:
        raise HTTPException(404, 'Participante não encontrado.')
    return result
