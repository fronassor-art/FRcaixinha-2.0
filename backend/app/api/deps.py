from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from jose import JWTError
from app.db.session import get_db
from app.core.security import decode_token
from app.core.config import settings
from app.models import User, UserSession

bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )

def current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
):
    if credentials is None:
        raise _unauthorized("Token ausente ou inválido")
    try:
        payload = decode_token(credentials.credentials)
        user_id = int(payload["sub"])
        jti = payload["jti"]
        if payload.get("purpose") == "2fa_challenge":
            raise _unauthorized("Autenticação de dois fatores pendente")
    except (JWTError, KeyError, ValueError):
        raise _unauthorized("Token inválido")
    now = datetime.now(timezone.utc)
    session = db.query(UserSession).filter(UserSession.jti == jti, UserSession.user_id == user_id).first()
    if not session or session.revoked_at is not None or session.expires_at <= now:
        raise _unauthorized("Sessão expirada ou revogada")
    if session.last_seen_at + timedelta(minutes=settings.session_idle_minutes) <= now:
        session.revoked_at = now
        db.commit()
        raise _unauthorized("Sessão expirada por inatividade")
    session.last_seen_at = now
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise _unauthorized("Usuário inativo ou inexistente")
    db.commit()
    return user

def require_admin(user=Depends(current_user)):
    if user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Acesso restrito ao administrador")
    return user


def require_master(user=Depends(current_user)):
    if not user.is_active or user.role != "ADMIN" or not user.is_master:
        raise HTTPException(status_code=403, detail="Acesso restrito ao Administrador Master")
    return user
