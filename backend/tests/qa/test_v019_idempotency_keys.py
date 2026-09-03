from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(name):
    return (ROOT / 'app' / 'api' / name).read_text()


def test_contribution_idempotency_key_is_deterministic():
    source = _read('payments.py')
    assert 'frc-contribution-{contribution.id}' in source
    assert 'uuid.uuid4()' not in source


def test_installment_idempotency_key_is_deterministic():
    source = _read('loan_installment_payments.py')
    assert "frc-loan-installment-{inst.id}" in source
    assert 'uuid.uuid4()' not in source
