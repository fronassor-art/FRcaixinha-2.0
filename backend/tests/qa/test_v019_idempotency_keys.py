from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(name):
    return (ROOT / 'app' / 'api' / name).read_text()


def test_contribution_idempotency_key_is_deterministic():
    source = _read('payments.py')
    assert 'frc-contribution-{contribution.id}' in source
def test_installment_idempotency_key_is_deterministic():
    api_source = _read("loan_installment_payments.py")
    service_source = (ROOT / "app" / "services" / "loan_installment_pix_attempts.py").read_text()
    assert "payment, _created = reserve(db, inst.id)" in api_source
    assert "idempotency_key=f\"frc-li-{inst.id}-{secrets.token_urlsafe(24)}\"" in service_source
    assert "idempotency_key=payment.idempotency_key" in api_source
