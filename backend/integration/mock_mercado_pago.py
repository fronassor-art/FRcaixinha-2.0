from fastapi import FastAPI, Header, HTTPException
from uuid import uuid4

app = FastAPI(title='FRcaixinha Mock Mercado Pago')
PAYMENTS = {}
IDEMPOTENCY = {}

@app.post('/v1/payments')
def create_payment(payload: dict, x_idempotency_key: str | None = Header(default=None)):
    if not x_idempotency_key:
        raise HTTPException(400, 'X-Idempotency-Key obrigatório')
    if x_idempotency_key in IDEMPOTENCY:
        return IDEMPOTENCY[x_idempotency_key]
    amount = payload.get('transaction_amount')
    if not amount or amount <= 0:
        raise HTTPException(400, 'transaction_amount inválido')
    pid = str(uuid4())
    result = {
        'id': pid, 'status': 'pending', 'status_detail': 'pending_waiting_transfer',
        'transaction_amount': amount,
        'point_of_interaction': {'transaction_data': {
            'qr_code': f'000201MOCK{pid}',
            'qr_code_base64': '',
            'ticket_url': f'http://mock.local/pay/{pid}'
        }}
    }
    PAYMENTS[pid] = result
    IDEMPOTENCY[x_idempotency_key] = result
    return result

@app.get('/v1/payments/{payment_id}')
def get_payment(payment_id: str):
    return PAYMENTS.get(payment_id) or {'id': payment_id, 'status': 'approved'}

@app.post('/__test__/payments/{payment_id}/approve')
def approve(payment_id: str):
    p = PAYMENTS.get(payment_id)
    if not p:
        raise HTTPException(404, 'payment not found')
    p['status'] = 'approved'
    p['status_detail'] = 'accredited'
    return p
