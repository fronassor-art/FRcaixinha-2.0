import os
import sys
from pathlib import Path
from fastapi.testclient import TestClient


# These tests intentionally exercise the mock provider contract independently of
# application credentials. Full E2E execution is provided by the compose sandbox.
os.environ.setdefault('DATABASE_URL', 'sqlite:///./sandbox_contracts.db')
os.environ.setdefault('JWT_SECRET', 'sandbox-secret')

from app.testing.mock_mercado_pago import app

client = TestClient(app)

def test_mock_pix_is_idempotent():
    payload = {'transaction_amount': 150.0, 'payment_method_id': 'pix', 'payer': {'email': 'teste@example.com'}}
    r1 = client.post('/v1/payments', json=payload, headers={'X-Idempotency-Key': 'same-key'})
    r2 = client.post('/v1/payments', json=payload, headers={'X-Idempotency-Key': 'same-key'})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()['id'] == r2.json()['id']

def test_mock_payment_can_be_approved():
    r = client.post('/v1/payments', json={'transaction_amount': 10.0}, headers={'X-Idempotency-Key': 'approve-key'})
    pid = r.json()['id']
    assert client.post(f'/__test__/payments/{pid}/approve').json()['status'] == 'approved'
    assert client.get(f'/v1/payments/{pid}').json()['status'] == 'approved'
