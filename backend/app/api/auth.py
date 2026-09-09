from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models import User, UserSession, LoginAttempt, PasswordResetToken
from app.schemas.auth import RegisterIn, LoginIn, TokenOut, PasswordChangeIn, PasswordResetRequestIn, PasswordResetConfirmIn, TwoFactorVerifyIn, TwoFactorCodeIn
from app.core.security import hash_password, verify_password, create_access_token, new_session_jti, new_reset_token, hash_reset_token
from app.core.config import settings
from app.services.notifications_v12 import create_notification, send_email
from app.models import UserSecurity, TrustedDevice, SecurityEvent
from app.services.security_v046 import new_totp_secret, encrypt_secret, decrypt_secret, verify_totp, provisioning_uri, make_recovery_codes, hash_recovery, new_challenge_token
import logging

log = logging.getLogger(__name__)
from app.api.deps import current_user

router = APIRouter(prefix="/auth", tags=["auth"])

def _now():
    return datetime.now(timezone.utc)

def _too_many_failures(db, email):
    cutoff = _now() - timedelta(minutes=15)
    return db.query(LoginAttempt).filter(LoginAttempt.email == email, LoginAttempt.success.is_(False), LoginAttempt.created_at >= cutoff).count() >= 5

def _record_login(db, email, request, success):
    db.add(LoginAttempt(email=email.lower(), ip_address=request.client.host if request.client else None, success=success))

def _new_session(db, user, request):
    now = _now()
    jti = new_session_jti()
    session = UserSession(user_id=user.id, jti=jti, created_at=now, last_seen_at=now,
                          expires_at=now + timedelta(minutes=settings.access_token_minutes),
                          ip_address=request.client.host if request.client else None,
                          user_agent=(request.headers.get("user-agent") or "")[:512])
    db.add(session)
    return create_access_token(str(user.id), user.role, jti=jti)

@router.post("/register", response_model=TokenOut)
def register(data: RegisterIn, request: Request, db: Session = Depends(get_db)):
    if not data.accept_terms:
        raise HTTPException(400, "É necessário aceitar os termos.")
    email = data.email.lower()
    cpf = "".join(c for c in data.cpf if c.isdigit())
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "E-mail já cadastrado.")
    if db.query(User).filter(User.cpf == cpf).first():
        raise HTTPException(409, "CPF já cadastrado.")
    user = User(name=data.name.strip(), email=email, cpf=cpf, phone=data.phone,
                password_hash=hash_password(data.password), role="USER", accepted_terms_at=_now())
    db.add(user); db.flush()
    create_notification(db, user.id, "WELCOME", "Bem-vindo à FRcaixinha", "Sua conta foi criada com sucesso.")
    token = _new_session(db, user, request)
    db.commit()
    return {"access_token": token, "token_type": "bearer"}

@router.post("/login", response_model=TokenOut)
def login(data: LoginIn, request: Request, db: Session = Depends(get_db)):
    email = data.email.lower()
    if _too_many_failures(db, email):
        raise HTTPException(429, "Muitas tentativas. Tente novamente mais tarde.")
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(data.password, user.password_hash):
        _record_login(db, email, request, False); db.commit()
        raise HTTPException(401, "E-mail ou senha inválidos.")
    _record_login(db, email, request, True)
    sec = db.query(UserSecurity).filter(UserSecurity.user_id == user.id).first()
    if sec and sec.totp_enabled:
        jti = new_session_jti()
        challenge = new_challenge_token(user.id, user.role, jti)
        db.add(SecurityEvent(user_id=user.id,event_type="LOGIN_2FA_CHALLENGE",severity="INFO",ip_address=request.client.host if request.client else None,user_agent=(request.headers.get("user-agent") or "")[:512]))
        db.commit()
        return {"access_token": challenge, "token_type": "bearer", "two_factor_required": True}
    token = _new_session(db, user, request)
    db.commit()
    return {"access_token": token, "token_type": "bearer", "two_factor_required": False}

@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db), user=Depends(current_user)):
    credentials = request.headers.get("authorization", "")
    token = credentials.split(" ", 1)[1] if credentials.lower().startswith("bearer ") else None
    if token:
        try:
            from app.core.security import decode_token
            jti = decode_token(token).get("jti")
            if jti:
                session = db.query(UserSession).filter(UserSession.jti == jti, UserSession.user_id == user.id).first()
                if session and not session.revoked_at:
                    session.revoked_at = _now()
                    db.commit()
        except Exception:
            pass
    return {"ok": True}

@router.post("/change-password")
def change_password(data: PasswordChangeIn, db: Session = Depends(get_db), user=Depends(current_user)):
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(400, "Senha atual inválida.")
    user.password_hash = hash_password(data.new_password)
    now = _now()
    db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).update({"revoked_at": now}, synchronize_session=False)
    db.commit()
    return {"ok": True, "message": "Senha alterada. Faça login novamente."}

@router.post("/password-reset/request")
def password_reset_request(data: PasswordResetRequestIn, request: Request, db: Session = Depends(get_db)):
    email = data.email.lower()
    user = db.query(User).filter(User.email == email).first()
    if user:
        # Invalidate previous outstanding tokens and issue a single-use token.
        now = _now()
        db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None)).update({"used_at": now}, synchronize_session=False)
        raw = new_reset_token()
        db.add(PasswordResetToken(user_id=user.id, token_hash=hash_reset_token(raw),
                                  expires_at=now + timedelta(minutes=settings.password_reset_minutes)))
        # Never persist the raw reset token in notifications or other application data.
        # Deliver it through the configured out-of-band email channel only.
        if settings.smtp_host:
            base_url = (settings.password_reset_base_url or "").rstrip("/")
            if base_url:
                link = f"{base_url}?token={raw}"
                body = ("Recebemos uma solicitação para redefinir sua senha na FRcaixinha.\n\n"
                        f"Use este link para continuar: {link}\n\n"
                        "Se você não solicitou isso, ignore esta mensagem.")
            else:
                body = ("Recebemos uma solicitação para redefinir sua senha na FRcaixinha.\n\n"
                        f"Seu código temporário é: {raw}\n\n"
                        "Se você não solicitou isso, ignore esta mensagem.")
            try:
                send_email(user, "Redefinição de senha — FRcaixinha", body)
            except Exception:
                log.warning("password_reset_email_delivery_failed user_id=%s", user.id)
    db.commit()
    return {"message": "Se o e-mail estiver cadastrado, você receberá as instruções de redefinição."}

@router.post("/password-reset/confirm")
def password_reset_confirm(data: PasswordResetConfirmIn, db: Session = Depends(get_db)):
    now = _now()
    token = db.query(PasswordResetToken).filter(PasswordResetToken.token_hash == hash_reset_token(data.token),
        PasswordResetToken.used_at.is_(None), PasswordResetToken.expires_at > now).first()
    if not token:
        raise HTTPException(400, "Token inválido ou expirado.")
    user = db.get(User, token.user_id)
    if not user or not user.is_active:
        raise HTTPException(400, "Token inválido ou expirado.")
    user.password_hash = hash_password(data.new_password)
    token.used_at = now
    db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).update({"revoked_at": now}, synchronize_session=False)
    db.commit()
    return {"ok": True, "message": "Senha redefinida. Faça login novamente."}


@router.post("/2fa/setup")
def two_factor_setup(request: Request, user=Depends(current_user), db: Session=Depends(get_db)):
    sec=db.query(UserSecurity).filter(UserSecurity.user_id==user.id).first()
    if not sec:
        sec=UserSecurity(user_id=user.id); db.add(sec); db.flush()
    secret=new_totp_secret(); sec.totp_secret=encrypt_secret(secret); sec.totp_enabled=False
    db.commit()
    return {"secret":secret,"otpauth_uri":provisioning_uri(secret,user),"message":"Escaneie o QR/provisioning URI e confirme com um código para ativar."}

@router.post("/2fa/enable")
def two_factor_enable(body: TwoFactorCodeIn, request: Request, user=Depends(current_user), db: Session=Depends(get_db)):
    sec=db.query(UserSecurity).filter(UserSecurity.user_id==user.id).first()
    if not sec or not sec.totp_secret: raise HTTPException(400,"2FA ainda não configurado.")
    if not verify_totp(decrypt_secret(sec.totp_secret),body.code): raise HTTPException(400,"Código 2FA inválido.")
    codes, hashes=make_recovery_codes(); sec.recovery_codes=",".join(hashes); sec.totp_enabled=True
    db.add(SecurityEvent(user_id=user.id,event_type="2FA_ENABLED",severity="INFO",ip_address=request.client.host if request.client else None,user_agent=(request.headers.get("user-agent") or "")[:512])); db.commit()
    return {"enabled":True,"recovery_codes":codes}

@router.post("/2fa/disable")
def two_factor_disable(body: TwoFactorCodeIn, request: Request, user=Depends(current_user), db: Session=Depends(get_db)):
    sec=db.query(UserSecurity).filter(UserSecurity.user_id==user.id).first()
    if not sec or not sec.totp_enabled or not verify_totp(decrypt_secret(sec.totp_secret),body.code): raise HTTPException(400,"Código 2FA inválido.")
    sec.totp_enabled=False; sec.recovery_codes=None; db.add(SecurityEvent(user_id=user.id,event_type="2FA_DISABLED",severity="WARNING",ip_address=request.client.host if request.client else None)); db.commit(); return {"enabled":False}

@router.post("/2fa/verify", response_model=TokenOut)
def two_factor_verify(body: TwoFactorVerifyIn, request: Request, db: Session=Depends(get_db)):
    from jose import jwt, JWTError
    try:
        payload=jwt.decode(body.challenge_token,settings.jwt_secret,algorithms=["HS256"])
        if payload.get("purpose")!="2fa_challenge": raise ValueError()
        user_id=int(payload["sub"]); jti=payload["jti"]
    except Exception: raise HTTPException(401,"Desafio 2FA inválido ou expirado.")
    user=db.get(User,user_id); sec=db.query(UserSecurity).filter(UserSecurity.user_id==user_id).first()
    valid=bool(user and sec and sec.totp_enabled and sec.totp_secret and verify_totp(decrypt_secret(sec.totp_secret),body.code))
    if not valid:
        db.add(SecurityEvent(user_id=user_id if 'user_id' in locals() else None,event_type="LOGIN_2FA_FAILED",severity="WARNING",ip_address=request.client.host if request.client else None))
        db.commit()
        raise HTTPException(401,"Código 2FA inválido.")
    token=_new_session(db,user,request); db.add(SecurityEvent(user_id=user.id,event_type="LOGIN_2FA_SUCCESS",severity="INFO",ip_address=request.client.host if request.client else None)); db.commit(); return {"access_token":token,"token_type":"bearer","two_factor_required":False}

@router.get("/2fa/status")
def two_factor_status(user=Depends(current_user), db:Session=Depends(get_db)):
    sec=db.query(UserSecurity).filter(UserSecurity.user_id==user.id).first(); return {"enabled":bool(sec and sec.totp_enabled),"configured":bool(sec and sec.totp_secret)}

@router.get("/security/events")
def security_events(limit:int=50,user=Depends(current_user),db:Session=Depends(get_db)):
    rows=db.query(SecurityEvent).filter(SecurityEvent.user_id==user.id).order_by(SecurityEvent.created_at.desc()).limit(max(1,min(limit,200))).all(); return {"items":[{"id":r.id,"type":r.event_type,"severity":r.severity,"created_at":r.created_at.isoformat()} for r in rows]}
