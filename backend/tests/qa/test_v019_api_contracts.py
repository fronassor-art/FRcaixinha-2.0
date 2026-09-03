from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_critical_routes_exist():
    main = (ROOT / 'app' / 'main.py').read_text()
    assert 'loan_installment_payments_router' in main
    assert 'payments_router' in main
    assert 'admin_router' in main


def test_webhook_uses_signature_validation_and_remote_status():
    source = (ROOT / 'app' / 'api' / 'payments.py').read_text()
    assert 'validate_mercado_pago_signature' in source
    assert 'await client.get_payment(data_id)' in source
    assert 'event_id' in source
