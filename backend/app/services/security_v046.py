from datetime import datetime, timezone
import base64, hashlib, hmac, secrets, struct, time
from cryptography.fernet import Fernet
from app.core.config import settings
from app.models import User, UserSession


def _fernet():
    key = hashlib.sha256(settings.jwt_secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))

def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()

def decrypt_secret(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()

def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')

def totp(secret: str, for_time: int | None = None, digits: int = 6) -> str:
    counter = int((for_time or int(time.time())) // 30)
    key = base64.b32decode(secret + '=' * ((8-len(secret)%8)%8), casefold=True)
    digest = hmac.new(key, struct.pack('>Q', counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    code = (struct.unpack('>I', digest[offset:offset+4])[0] & 0x7fffffff) % (10**digits)
    return str(code).zfill(digits)

def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    now = int(time.time())
    return any(hmac.compare_digest(totp(secret, now + offset*30), code.strip()) for offset in range(-window, window+1))

def provisioning_uri(secret: str, user: User, issuer='FRcaixinha') -> str:
    import urllib.parse
    label = urllib.parse.quote(f'{issuer}:{user.email}')
    issuer_q = urllib.parse.quote(issuer)
    return f'otpauth://totp/{label}?secret={secret}&issuer={issuer_q}&algorithm=SHA1&digits=6&period=30'

def hash_recovery(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()

def make_recovery_codes(n=8):
    codes=[secrets.token_hex(4).upper() for _ in range(n)]
    return codes, [hash_recovery(c) for c in codes]

def new_challenge_token(user_id: int, role: str, jti: str):
    from app.core.security import create_access_token
    from jose import jwt
    token = create_access_token(str(user_id), role, jti=jti)
    payload = jwt.get_unverified_claims(token)
    payload['purpose']='2fa_challenge'
    payload['exp']=datetime.now(timezone.utc).timestamp()+300
    return jwt.encode(payload, settings.jwt_secret, algorithm='HS256')
