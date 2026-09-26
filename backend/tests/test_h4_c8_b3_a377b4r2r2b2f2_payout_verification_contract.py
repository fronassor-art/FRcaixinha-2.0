from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core.payout_destination_verification import (
    DestinationFreshness,
    VerificationAttempt,
    VerificationContractError,
    VerificationIdempotencyConflict,
    VerificationOutcome,
    VerificationResult,
    VerificationResultState,
    evaluate_destination_freshness,
    validate_idempotency_reuse,
)


NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
SENSITIVE_KEY = "52998224725"
DIGEST = "sha256:" + "a" * 64


def _attempt(**changes):
    values = {
        "attempt_id": "attempt-1",
        "idempotency_key": "idem-1",
        "destination_id": 7,
        "destination_version": 2,
        "key_type": "CPF",
        "normalized_key": SENSITIVE_KEY,
        "requested_at": NOW,
    }
    values.update(changes)
    return VerificationAttempt(**values)


def _result(outcome, **changes):
    values = {
        "attempt_id": "attempt-1",
        "state": VerificationResultState.FINAL,
        "outcome": outcome,
        "provider_name": "provider-neutral",
        "received_at": NOW,
    }
    values.update(changes)
    return VerificationResult(**values)


def test_valid_request_is_immutable_and_transient():
    request = _attempt()
    assert request.destination_id == 7
    assert request.destination_version == 2
    assert request.normalized_key == SENSITIVE_KEY
    with pytest.raises((AttributeError, TypeError)):
        request.destination_version = 3


@pytest.mark.parametrize(
    "changes",
    [
        {"destination_id": 0},
        {"destination_id": True},
        {"destination_version": -1},
        {"requested_at": datetime(2026, 9, 26, 12, 0)},
        {"idempotency_key": "  "},
        {"key_type": "invalid_chave_pix"},
    ],
)
def test_invalid_request_values_are_rejected(changes):
    with pytest.raises(VerificationContractError):
        _attempt(**changes)


@pytest.mark.parametrize(
    "outcome",
    [
        VerificationOutcome.CONFIRMED,
        VerificationOutcome.NOT_CONFIRMED,
        VerificationOutcome.RETRYABLE,
        VerificationOutcome.AMBIGUOUS,
        VerificationOutcome.INVALID_RESPONSE,
    ],
)
def test_all_canonical_final_outcomes_are_valid(outcome):
    assert _result(outcome).outcome is outcome


def test_pending_result_has_no_ownership_outcome():
    pending = VerificationResult(
        attempt_id="attempt-1",
        state=VerificationResultState.PENDING,
        outcome=None,
        provider_name="provider-neutral",
        received_at=NOW,
        provider_request_id="request-1",
    )
    assert pending.state is VerificationResultState.PENDING
    assert pending.outcome is None
    with pytest.raises(VerificationContractError):
        VerificationResult(
            attempt_id="attempt-1",
            state=VerificationResultState.PENDING,
            outcome=VerificationOutcome.CONFIRMED,
            provider_name="provider-neutral",
            received_at=NOW,
        )


def test_final_result_requires_canonical_outcome():
    with pytest.raises(VerificationContractError):
        _result("CONFIRMED")


def test_naive_provider_timestamp_is_rejected():
    with pytest.raises(VerificationContractError):
        _result(
            VerificationOutcome.CONFIRMED,
            provider_timestamp=datetime(2026, 9, 26, 12, 0),
        )


def test_result_digest_format_is_strict():
    assert _result(VerificationOutcome.CONFIRMED, evidence_digest=DIGEST).evidence_digest == DIGEST
    with pytest.raises(VerificationContractError):
        _result(VerificationOutcome.CONFIRMED, evidence_digest=SENSITIVE_KEY)


def test_same_idempotency_key_and_destination_identity_is_reusable():
    assert validate_idempotency_reuse(_attempt(), _attempt(attempt_id="attempt-2")) is True
    assert validate_idempotency_reuse(
        _attempt(), _attempt(idempotency_key="idem-2")
    ) is False


@pytest.mark.parametrize(
    "changes",
    [
        {"destination_id": 8},
        {"destination_version": 3},
        {"key_type": "EMAIL"},
    ],
)
def test_idempotency_reuse_for_another_identity_conflicts(changes):
    with pytest.raises(VerificationIdempotencyConflict) as exc_info:
        validate_idempotency_reuse(_attempt(), _attempt(**changes))
    assert SENSITIVE_KEY not in str(exc_info.value)


def test_destination_freshness_states():
    request = _attempt()
    assert evaluate_destination_freshness(
        request,
        current_destination_id=7,
        current_destination_version=2,
        current_status="UNVERIFIED",
    ) is DestinationFreshness.CURRENT
    assert evaluate_destination_freshness(
        request,
        current_destination_id=7,
        current_destination_version=3,
        current_status="UNVERIFIED",
    ) is DestinationFreshness.STALE
    assert evaluate_destination_freshness(
        request,
        current_destination_id=7,
        current_destination_version=2,
        current_status="REVOKED",
    ) is DestinationFreshness.REVOKED
    assert evaluate_destination_freshness(
        request,
        current_destination_id=7,
        current_destination_version=2,
        current_status="VERIFIED",
    ) is DestinationFreshness.NOT_UNVERIFIED


def test_repr_and_domain_errors_do_not_reveal_normalized_key():
    request = _attempt()
    assert SENSITIVE_KEY not in repr(request)
    with pytest.raises(VerificationContractError) as exc_info:
        _attempt(normalized_key="   ")
    assert SENSITIVE_KEY not in str(exc_info.value)


def test_digest_is_supplied_and_not_derived_from_key():
    first = _attempt(normalized_key=SENSITIVE_KEY)
    second = _attempt(normalized_key="11144477735")
    first_result = _result(VerificationOutcome.CONFIRMED, evidence_digest=DIGEST)
    second_result = _result(VerificationOutcome.CONFIRMED, evidence_digest=DIGEST)
    assert first_result.evidence_digest == second_result.evidence_digest
    assert first.normalized_key != second.normalized_key


def test_production_module_imports_only_pure_standard_library_modules():
    source_path = Path(__file__).parents[1] / "app/core/payout_destination_verification.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert imported <= {"__future__", "re", "dataclasses", "datetime", "enum", "typing"}
    assert not imported & {"sqlalchemy", "fastapi", "app", "mercado_pago"}
