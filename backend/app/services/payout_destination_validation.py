"""Local format validation only; it does not establish key ownership."""

from __future__ import annotations

import re
from uuid import UUID

from email_validator import EmailNotValidError, validate_email


PIX_KEY_TYPES = frozenset({"CPF", "PHONE", "EMAIL", "EVP"})
_CPF_DIGITS = re.compile(r"^[0-9]{11}$")
_CPF_FORMATTED = re.compile(r"^[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2}$")
_BR_E164 = re.compile(r"^\+55[1-9][0-9](?:[2-5][0-9]{7}|9[0-9]{8})$")
_UUID_CANONICAL = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class PayoutDestinationValidationError(ValueError):
    """A PIX key type or local representation is invalid."""


def normalize_key_type(key_type: str) -> str:
    if not isinstance(key_type, str):
        raise PayoutDestinationValidationError("Unsupported PIX key type.")
    normalized = key_type.strip().upper()
    if normalized not in PIX_KEY_TYPES:
        raise PayoutDestinationValidationError("Unsupported PIX key type.")
    return normalized


def _valid_cpf(digits: str) -> bool:
    if not _CPF_DIGITS.fullmatch(digits) or len(set(digits)) == 1:
        return False
    first_sum = sum(int(digits[i]) * (10 - i) for i in range(9))
    first_check = 0 if first_sum % 11 < 2 else 11 - first_sum % 11
    second_sum = sum(int(digits[i]) * (11 - i) for i in range(10))
    second_check = 0 if second_sum % 11 < 2 else 11 - second_sum % 11
    return digits[-2:] == f"{first_check}{second_check}"


def normalize_payout_destination(key_type: str, value: str) -> str:
    kind = normalize_key_type(key_type)
    if not isinstance(value, str):
        raise PayoutDestinationValidationError("PIX key format is invalid.")
    candidate = value.strip()
    if kind == "CPF":
        if _CPF_FORMATTED.fullmatch(candidate):
            digits = candidate.replace(".", "").replace("-", "")
        elif _CPF_DIGITS.fullmatch(candidate):
            digits = candidate
        else:
            raise PayoutDestinationValidationError("CPF format is invalid.")
        if not _valid_cpf(digits):
            raise PayoutDestinationValidationError("CPF is invalid.")
        return digits
    if kind == "PHONE":
        if not _BR_E164.fullmatch(candidate):
            raise PayoutDestinationValidationError(
                "Phone key must be a valid Brazilian E.164 number."
            )
        return candidate
    if kind == "EMAIL":
        try:
            return validate_email(candidate, check_deliverability=False).normalized
        except EmailNotValidError:
            raise PayoutDestinationValidationError("Email key is invalid.") from None
    if kind == "EVP":
        if not _UUID_CANONICAL.fullmatch(candidate):
            raise PayoutDestinationValidationError("EVP key must be a UUID.")
        try:
            return str(UUID(candidate)).lower()
        except ValueError:
            raise PayoutDestinationValidationError("EVP key must be a UUID.") from None
    raise PayoutDestinationValidationError("Unsupported PIX key type.")
