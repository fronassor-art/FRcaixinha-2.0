"""Read-only, aggregate-only classification of legacy user identities."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.orm import Session

from app.core.cpf import CPFValidationError, cpf_storage_candidates, normalize_cpf
from app.models import User
from app.services.privacy_v045 import _privacy_cpf_tombstone


_EMAIL_ADAPTER = TypeAdapter(EmailStr)
_EMAIL_PRIVACY_TOMBSTONE = re.compile(
    r"^anon-([1-9][0-9]{0,9})-([0-9a-f]{12})@anon\.invalid$"
)


class _CpfCategory(str, Enum):
    CANONICAL = "cpf_canonical_valid"
    FORMATTED = "cpf_formatted_valid_legacy"
    INVALID_ACTIVE = "cpf_invalid_active"
    PRIVACY_TOMBSTONE = "cpf_privacy_tombstone_valid"
    PRIVACY_TOMBSTONE_MALFORMED = "cpf_privacy_tombstone_malformed"
    UNKNOWN = "cpf_unknown_legacy"


class _EmailCategory(str, Enum):
    CANONICAL = "email_canonical"
    MIXED_CASE = "email_mixed_case_legacy"
    PRIVACY_TOMBSTONE = "email_privacy_tombstone_compatible"
    PRIVACY_TOMBSTONE_MALFORMED = "email_privacy_tombstone_malformed"
    INVALID_OR_UNKNOWN = "email_invalid_or_unknown_legacy"


@dataclass(frozen=True)
class LegacyIdentityPreflightReport:
    """Aggregate counts only; the report never contains identity values."""

    total_users: int
    active_users: int

    cpf_canonical_valid: int
    cpf_formatted_valid_legacy: int
    cpf_invalid_active: int
    cpf_privacy_tombstone_valid: int
    cpf_privacy_tombstone_malformed: int
    cpf_unknown_legacy: int
    cpf_logical_collision_groups: int
    cpf_logical_collision_users: int

    email_canonical: int
    email_mixed_case_legacy: int
    email_privacy_tombstone_compatible: int
    email_privacy_tombstone_malformed: int
    email_invalid_or_unknown_legacy: int
    email_case_collision_groups: int
    email_case_collision_users: int


def _looks_like_cpf_tombstone(value: Any) -> bool:
    return isinstance(value, str) and value.lstrip().upper().startswith("ANON")


def _classify_cpf(
    user_id: int,
    raw: Any,
    is_active: bool,
) -> tuple[_CpfCategory, str | None]:
    """Classify one CPF and return an internal-only logical key if valid."""
    expected_tombstone = None
    try:
        expected_tombstone = _privacy_cpf_tombstone(user_id)
    except (TypeError, ValueError):
        pass

    if raw == expected_tombstone and expected_tombstone is not None:
        if not is_active:
            return _CpfCategory.PRIVACY_TOMBSTONE, None
        return _CpfCategory.PRIVACY_TOMBSTONE_MALFORMED, None

    if _looks_like_cpf_tombstone(raw):
        return _CpfCategory.PRIVACY_TOMBSTONE_MALFORMED, None

    try:
        logical_key = normalize_cpf(raw)
    except CPFValidationError:
        if is_active:
            return _CpfCategory.INVALID_ACTIVE, None
        return _CpfCategory.UNKNOWN, None

    if raw == logical_key:
        return _CpfCategory.CANONICAL, logical_key

    formatted = cpf_storage_candidates(logical_key)[1]
    if raw == formatted:
        return _CpfCategory.FORMATTED, logical_key

    # normalize_cpf() strips outer whitespace. Such stored values can be
    # grouped logically, but are not either exact storage representation.
    return _CpfCategory.UNKNOWN, logical_key


def _looks_like_email_tombstone(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    candidate = value.lower()
    return candidate.startswith("anon-") or candidate.endswith("@anon.invalid")


def _normalize_email_lookup_key(raw: Any) -> str | None:
    """Return the current EmailStr-then-lowercase lookup form, internally."""
    try:
        validated = _EMAIL_ADAPTER.validate_python(raw)
    except (TypeError, ValueError, ValidationError):
        return None
    return str(validated).lower()


def _classify_email(
    user_id: int,
    raw: Any,
    is_active: bool,
) -> tuple[_EmailCategory, str | None]:
    """Classify one email and return an internal-only lookup key if valid."""
    if _looks_like_email_tombstone(raw):
        match = _EMAIL_PRIVACY_TOMBSTONE.fullmatch(raw) if isinstance(raw, str) else None
        # The truncated digest depends on the pre-anonymization email and
        # cannot be verified from this tombstone. Compatibility is format,
        # embedded ID, and inactive state only.
        compatible = (
            match is not None
            and int(match.group(1)) == user_id
            and not is_active
        )
        if compatible:
            return _EmailCategory.PRIVACY_TOMBSTONE, None
        return _EmailCategory.PRIVACY_TOMBSTONE_MALFORMED, None

    lookup_key = _normalize_email_lookup_key(raw)
    if lookup_key is None or not isinstance(raw, str):
        return _EmailCategory.INVALID_OR_UNKNOWN, None

    if raw == lookup_key:
        return _EmailCategory.CANONICAL, lookup_key
    if raw.lower() == lookup_key:
        return _EmailCategory.MIXED_CASE, lookup_key

    # The validator accepted the value, so keep the current lookup key for
    # collision detection, but avoid classifying other normalization as a
    # simple case-only legacy difference.
    return _EmailCategory.INVALID_OR_UNKNOWN, lookup_key


def _collision_totals(keys: Counter[str]) -> tuple[int, int]:
    collided = [count for count in keys.values() if count > 1]
    return len(collided), sum(collided)


def evaluate_legacy_identity_preflight(
    db: Session,
) -> LegacyIdentityPreflightReport:
    """Read identity columns and return aggregate classifications only.

    The query is limited to the four required columns and runs in
    ``no_autoflush`` so pending ORM state is never written as a side effect.
    This function does not commit, roll back, mutate objects, or log values.
    """
    with db.no_autoflush:
        rows = db.query(User.id, User.cpf, User.email, User.is_active).all()

    cpf_counts: Counter[_CpfCategory] = Counter()
    email_counts: Counter[_EmailCategory] = Counter()
    cpf_keys: Counter[str] = Counter()
    email_keys: Counter[str] = Counter()
    active_users = 0

    for row in rows:
        user_id, raw_cpf, raw_email, is_active = row
        active = bool(is_active)
        active_users += int(active)

        cpf_category, cpf_key = _classify_cpf(user_id, raw_cpf, active)
        cpf_counts[cpf_category] += 1
        if cpf_key is not None:
            cpf_keys[cpf_key] += 1

        email_category, email_key = _classify_email(user_id, raw_email, active)
        email_counts[email_category] += 1
        if email_key is not None:
            email_keys[email_key] += 1

    cpf_collision_groups, cpf_collision_users = _collision_totals(cpf_keys)
    email_collision_groups, email_collision_users = _collision_totals(email_keys)

    return LegacyIdentityPreflightReport(
        total_users=len(rows),
        active_users=active_users,
        cpf_canonical_valid=cpf_counts[_CpfCategory.CANONICAL],
        cpf_formatted_valid_legacy=cpf_counts[_CpfCategory.FORMATTED],
        cpf_invalid_active=cpf_counts[_CpfCategory.INVALID_ACTIVE],
        cpf_privacy_tombstone_valid=cpf_counts[_CpfCategory.PRIVACY_TOMBSTONE],
        cpf_privacy_tombstone_malformed=cpf_counts[
            _CpfCategory.PRIVACY_TOMBSTONE_MALFORMED
        ],
        cpf_unknown_legacy=cpf_counts[_CpfCategory.UNKNOWN],
        cpf_logical_collision_groups=cpf_collision_groups,
        cpf_logical_collision_users=cpf_collision_users,
        email_canonical=email_counts[_EmailCategory.CANONICAL],
        email_mixed_case_legacy=email_counts[_EmailCategory.MIXED_CASE],
        email_privacy_tombstone_compatible=email_counts[
            _EmailCategory.PRIVACY_TOMBSTONE
        ],
        email_privacy_tombstone_malformed=email_counts[
            _EmailCategory.PRIVACY_TOMBSTONE_MALFORMED
        ],
        email_invalid_or_unknown_legacy=email_counts[
            _EmailCategory.INVALID_OR_UNKNOWN
        ],
        email_case_collision_groups=email_collision_groups,
        email_case_collision_users=email_collision_users,
    )
