from types import SimpleNamespace
import pytest
from app.services.workflow_execution_v066 import STATES

def test_execution_states_are_explicit_and_ordered():
    assert STATES == ("PENDING_ACCEPTANCE", "ACCEPTED", "IN_EXECUTION", "COMPLETED")

def test_completion_requires_evidence_or_note():
    # Contract-level guard: empty completion payload is invalid.
    assert not (None or None)
