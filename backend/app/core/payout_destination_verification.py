"""Provider-neutral contract for payout destination ownership checks.

This module defines transient domain values only. It does not persist evidence,
call a provider, or change a destination's lifecycle state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol


class VerificationContractError(ValueError):
    """A provider-neutral verification value violates this contract."""


class VerificationIdempotencyConflict(VerificationContractError):
    """An idempotency key was reused for a different destination identity."""


class VerificationOutcome(str, Enum):
    """Canonical outcomes; adapters translate provider-specific statuses."""

    CONFIRMED = "CONFIRMED"
    NOT_CONFIRMED = "NOT_CONFIRMED"
    RETRYABLE = "RETRYABLE"
    AMBIGUOUS = "AMBIGUOUS"
    INVALID_RESPONSE = "INVALID_RESPONSE"


class VerificationResultState(str, Enum):
    """Whether a provider response is pending or contains a final outcome."""

    PENDING = "PENDING"
    FINAL = "FINAL"


class DestinationFreshness(str, Enum):
    """Whether a result still applies to the currently active destination."""

    CURRENT = "CURRENT"
    STALE = "STALE"
    REVOKED = "REVOKED"
    NOT_UNVERIFIED = "NOT_UNVERIFIED"


_KEY_TYPES = frozenset({"CPF", "PHONE", "EMAIL", "EVP"})
_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_DESTINATION_STATES = frozenset({"UNVERIFIED", "VERIFIED", "REVOKED"})


def _require_nonblank(value: str, *, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise VerificationContractError(f"{name} must be a non-empty string.")


def _require_positive_id(value: int, *, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise VerificationContractError(f"{name} must be a positive integer.")


def _require_aware(value: datetime, *, name: str) -> None:
    if not isinstance(value, datetime):
        raise VerificationContractError(f"{name} must be a datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise VerificationContractError(f"{name} must be timezone-aware.")


@dataclass(frozen=True, repr=False)
class VerificationAttempt:
    """Transient request to verify one exact destination version.

    ``normalized_key`` is passed only to an adapter when needed. It is excluded
    from repr and is neither serialized nor persisted by this module.
    """

    attempt_id: str
    idempotency_key: str
    destination_id: int
    destination_version: int
    key_type: str
    normalized_key: str = field(repr=False)
    requested_at: datetime

    def __post_init__(self) -> None:
        _require_nonblank(self.attempt_id, name="attempt_id")
        _require_nonblank(self.idempotency_key, name="idempotency_key")
        _require_positive_id(self.destination_id, name="destination_id")
        _require_positive_id(self.destination_version, name="destination_version")
        if not isinstance(self.key_type, str) or self.key_type not in _KEY_TYPES:
            raise VerificationContractError("key_type is unsupported.")
        _require_nonblank(self.normalized_key, name="normalized_key")
        _require_aware(self.requested_at, name="requested_at")


@dataclass(frozen=True)
class VerificationResult:
    """Provider response normalized to this domain's safe vocabulary.

    A pending result has no ownership outcome. A final result must have one;
    even ``CONFIRMED`` is only evidence for a later, separate state transition.
    """

    attempt_id: str
    state: VerificationResultState
    outcome: VerificationOutcome | None
    provider_name: str
    received_at: datetime
    provider_request_id: str | None = None
    provider_response_id: str | None = None
    provider_event_id: str | None = None
    provider_timestamp: datetime | None = None
    reason_code: str | None = None
    evidence_digest: str | None = None

    def __post_init__(self) -> None:
        _require_nonblank(self.attempt_id, name="attempt_id")
        _require_nonblank(self.provider_name, name="provider_name")
        _require_aware(self.received_at, name="received_at")
        if not isinstance(self.state, VerificationResultState):
            raise VerificationContractError("state is invalid.")
        if self.state is VerificationResultState.PENDING:
            if self.outcome is not None:
                raise VerificationContractError("pending result cannot have an outcome.")
        elif not isinstance(self.outcome, VerificationOutcome):
            raise VerificationContractError("final result requires a valid outcome.")

        for name in ("provider_request_id", "provider_response_id", "provider_event_id"):
            value = getattr(self, name)
            if value is not None:
                _require_nonblank(value, name=name)
        if self.provider_timestamp is not None:
            _require_aware(self.provider_timestamp, name="provider_timestamp")
        if self.reason_code is not None:
            _require_nonblank(self.reason_code, name="reason_code")
        if self.evidence_digest is not None and not _SHA256_DIGEST.fullmatch(
            self.evidence_digest
        ):
            raise VerificationContractError("evidence_digest must use sha256:<64 lowercase hex>.")


class PayoutDestinationVerificationProvider(Protocol):
    """Adapter interface; implementations must return normalized results."""

    def verify_ownership(self, request: VerificationAttempt) -> VerificationResult:
        """Start a check or return its result; asynchronous work may be PENDING."""
        ...


def validate_idempotency_reuse(
    existing: VerificationAttempt,
    candidate: VerificationAttempt,
) -> bool:
    """Return whether the candidate reuses the same logical attempt.

    Different idempotency keys are distinct attempts and return ``False``.
    Reusing a key for another destination, version, or key type is a domain
    conflict. The key itself is never included in an exception.
    """

    if existing.idempotency_key != candidate.idempotency_key:
        return False
    identity = (existing.destination_id, existing.destination_version, existing.key_type)
    candidate_identity = (
        candidate.destination_id,
        candidate.destination_version,
        candidate.key_type,
    )
    if identity != candidate_identity:
        raise VerificationIdempotencyConflict(
            "idempotency key is already bound to another destination identity."
        )
    return True


def evaluate_destination_freshness(
    attempt: VerificationAttempt,
    *,
    current_destination_id: int,
    current_destination_version: int,
    current_status: str,
) -> DestinationFreshness:
    """Reject results for replaced, revoked, or no-longer-unverified rows."""

    _require_positive_id(current_destination_id, name="current_destination_id")
    _require_positive_id(current_destination_version, name="current_destination_version")
    if current_status not in _DESTINATION_STATES:
        raise VerificationContractError("current_status is invalid.")
    if (
        attempt.destination_id != current_destination_id
        or attempt.destination_version != current_destination_version
    ):
        return DestinationFreshness.STALE
    if current_status == "REVOKED":
        return DestinationFreshness.REVOKED
    if current_status != "UNVERIFIED":
        return DestinationFreshness.NOT_UNVERIFIED
    return DestinationFreshness.CURRENT


__all__ = [
    "DestinationFreshness",
    "PayoutDestinationVerificationProvider",
    "VerificationAttempt",
    "VerificationContractError",
    "VerificationIdempotencyConflict",
    "VerificationOutcome",
    "VerificationResult",
    "VerificationResultState",
    "evaluate_destination_freshness",
    "validate_idempotency_reuse",
]
