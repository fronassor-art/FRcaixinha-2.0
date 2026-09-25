"""Encryption and safe display helpers for member PIX destinations."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class PayoutDestinationCryptoError(ValueError):
    """A destination could not be encrypted or decrypted safely."""


def _fernet() -> Fernet:
    key = settings.payout_destination_encryption_key
    if not isinstance(key, str) or not key:
        raise PayoutDestinationCryptoError(
            "Payout destination encryption key is not configured."
        )
    try:
        return Fernet(key.encode("ascii"))
    except (UnicodeEncodeError, TypeError, ValueError) as exc:
        raise PayoutDestinationCryptoError(
            "Payout destination encryption key is invalid."
        ) from None


def encrypt_payout_destination(normalized_value: str) -> str:
    if not isinstance(normalized_value, str) or not normalized_value:
        raise PayoutDestinationCryptoError("Payout destination value is invalid.")
    try:
        return _fernet().encrypt(normalized_value.encode("utf-8")).decode("ascii")
    except (UnicodeEncodeError, ValueError, TypeError) as exc:
        if isinstance(exc, PayoutDestinationCryptoError):
            raise
        raise PayoutDestinationCryptoError(
            "Payout destination encryption failed."
        ) from None


def decrypt_payout_destination(ciphertext: str) -> str:
    if not isinstance(ciphertext, str) or not ciphertext:
        raise PayoutDestinationCryptoError("Payout destination ciphertext is invalid.")
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError, TypeError, ValueError):
        raise PayoutDestinationCryptoError(
            "Payout destination could not be decrypted."
        ) from None


def mask_payout_destination(key_type: str, normalized_value: str) -> str:
    if not isinstance(normalized_value, str) or not normalized_value:
        raise ValueError("Payout destination value is invalid.")
    kind = key_type.strip().upper() if isinstance(key_type, str) else ""
    if kind == "CPF":
        return f"***.***.***-{normalized_value[-2:]}"
    if kind == "PHONE":
        return f"+55*********{normalized_value[-4:]}"
    if kind == "EMAIL":
        local, separator, domain = normalized_value.rpartition("@")
        if not separator or not local or not domain:
            raise ValueError("Payout destination email is invalid.")
        return f"{local[0]}***@{domain}"
    if kind == "EVP":
        return f"********-****-****-****-********{normalized_value[-4:]}"
    raise ValueError("Unsupported PIX key type.")
