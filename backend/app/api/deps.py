from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from jose import JWTError
from app.db.session import get_db
from app.core.security import decode_token
from app.core.config import settings
from app.models import User, UserSession

bearer = HTTPBearer()

def current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
):
    try:
        payload = decode_token(credentials.credentials)
        user_id = int(payload["sub"])
        jti = payload["jti"]
        if payload.get("purpose") == "2fa_challenge":
            raise HTTPException(status_code=401, detail="Autenticação de dois fatores pendente")
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")
    now = datetime.now(timezone.utc)
    session = db.query(UserSession).filter(UserSession.jti == jti, UserSession.user_id == user_id).first()
    if not session or session.revoked_at is not None or session.expires_at <= now:
        raise HTTPException(status_code=401, detail="Sessão expirada ou revogada")
    if session.last_seen_at + timedelta(minutes=settings.session_idle_minutes) <= now:
        session.revoked_at = now
        db.commit()
        raise HTTPException(status_code=401, detail="Sessão expirada por inatividade")
    session.last_seen_at = now
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Usuário inativo ou inexistente")
    db.commit()
    return user

def require_admin(user=Depends(current_user)):
    if user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Acesso restrito ao administrador")
    return user
