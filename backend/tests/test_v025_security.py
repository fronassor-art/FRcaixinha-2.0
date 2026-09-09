from datetime import datetime, timezone
from app.core.security import hash_password, verify_password, new_reset_token, hash_reset_token, create_access_token, decode_token

def test_password_hash_roundtrip():
    hashed = hash_password("UmaSenhaMuitoForte123!")
    assert hashed != "UmaSenhaMuitoForte123!"
    assert verify_password("UmaSenhaMuitoForte123!", hashed)
    assert not verify_password("errada", hashed)

def test_reset_token_is_random_and_hashed():
    token = new_reset_token()
    assert len(token) >= 40
    assert hash_reset_token(token) != token
    assert hash_reset_token(token) == hash_reset_token(token)

def test_jwt_has_jti_and_expiry():
    token = create_access_token("123", "USER", jti="test-jti")
    payload = decode_token(token)
    assert payload["sub"] == "123"
    assert payload["role"] == "USER"
    assert payload["jti"] == "test-jti"
    assert payload["exp"] > int(datetime.now(timezone.utc).timestamp())
