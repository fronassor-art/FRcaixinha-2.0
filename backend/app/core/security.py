from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from jose import jwt, JWTError
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from app.core.config import settings

ALGORITHM = "HS256"
ph = PasswordHasher()

def hash_password(password: str) -> str:
    return ph.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    try:
        return ph.verify(hashed, password)
    except VerifyMismatchError:
        return False

def create_access_token(subject: str, role: str, jti: str | None = None) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=settings.access_token_minutes)
    payload = {"sub": subject, "role": role, "iat": now, "exp": exp, "jti": jti or secrets.token_urlsafe(32)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)

def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])

def new_session_jti() -> str:
    return secrets.token_urlsafe(32)

def new_reset_token() -> str:
    return secrets.token_urlsafe(48)

def hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
