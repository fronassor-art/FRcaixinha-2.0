"""Canonical, provider-neutral Brazilian CPF normalization and validation."""

from __future__ import annotations

import re


class CPFValidationError(ValueError):
    """Raised when a CPF is not in an accepted format or fails its checksum."""


_CPF_DIGITS = re.compile(r"^[0-9]{11}$")
_CPF_FORMATTED = re.compile(r"^[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2}$")


def _has_valid_check_digits(digits: str) -> bool:
    if len(set(digits)) == 1:
        return False

    first_sum = sum(int(digits[index]) * (10 - index) for index in range(9))
    first_check = 0 if first_sum % 11 < 2 else 11 - first_sum % 11

    second_sum = sum(int(digits[index]) * (11 - index) for index in range(10))
    second_check = 0 if second_sum % 11 < 2 else 11 - second_sum % 11

    return digits[-2:] == f"{first_check}{second_check}"


def normalize_cpf(value: str) -> str:
    """Accept a plain or exactly formatted CPF and return 11 ASCII digits."""
    if not isinstance(value, str):
        raise CPFValidationError("CPF inválido.")

    candidate = value.strip()
    if _CPF_DIGITS.fullmatch(candidate):
        digits = candidate
    elif _CPF_FORMATTED.fullmatch(candidate):
        digits = candidate.replace(".", "").replace("-", "")
    else:
        raise CPFValidationError("CPF inválido.")

    if not _has_valid_check_digits(digits):
        raise CPFValidationError("CPF inválido.")

    return digits
