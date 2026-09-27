import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from app.services import mercado_pago


def _run_create(monkeypatch, response_handler, *, error_match=None):
    requests = []
    real_async_client = httpx.AsyncClient

    def handler(request):
        requests.append(request)
        return response_handler(request)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        mercado_pago.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(transport=transport, **kwargs),
    )

    client = mercado_pago.MercadoPagoClient()
    client.token = "synthetic-token"

    async def invoke():
        return await client.create_pix_payment(
            amount=Decimal("12.30"),
            email="payer@example.invalid",
            cpf="11144477735",
            description="synthetic test payment",
            idempotency_key="idem-exact-value",
            external_reference="external-exact-value",
        )

    if error_match is None:
        result = asyncio.run(invoke())
        return result, requests

    with pytest.raises(error_match) as captured:
        asyncio.run(invoke())
    return captured.value, requests


def _json_response(status_code, payload):
    return lambda request: httpx.Response(status_code, json=payload, request=request)


def test_create_pix_payment_accepts_valid_order_and_preserves_request(monkeypatch):
    result, requests = _run_create(
        monkeypatch,
        _json_response(
            201,
            {
                "id": "order-synthetic-1",
                "status": "created",
                "transactions": {
                    "payments": [
                        {
                            "id": "payment-synthetic-1",
                            "status": "pending",
                            "payment_method": {"qr_code": "synthetic-qr"},
                        }
                    ]
                },
            },
        ),
    )

    assert result["order_id"] == "order-synthetic-1"
    assert result["id"] == "payment-synthetic-1"
    request = requests[0]
    assert request.headers["X-Idempotency-Key"] == "idem-exact-value"
    body = json.loads(request.content)
    assert body["external_reference"] == "external-exact-value"
    assert body["total_amount"] == "12.30"
    assert body["transactions"]["payments"][0]["amount"] == "12.30"


def test_create_pix_payment_request_error_is_ambiguous_and_sanitized(monkeypatch):
    def handler(request):
        raise httpx.ConnectError(
            "Authorization Bearer synthetic-token payer@example.invalid 11144477735",
            request=request,
        )

    error, _ = _run_create(
        monkeypatch,
        handler,
        error_match=mercado_pago.ProviderCreateAmbiguity,
    )
    message = str(error)
    assert "transport failure" in message
    assert "synthetic-token" not in message
    assert "payer@example.invalid" not in message
    assert "11144477735" not in message


@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_create_pix_payment_server_errors_are_ambiguous_and_sanitized(
    monkeypatch, status_code
):
    error, _ = _run_create(
        monkeypatch,
        lambda request: httpx.Response(
            status_code,
            text="sensitive response body payer@example.invalid 11144477735",
            request=request,
        ),
        error_match=mercado_pago.ProviderCreateAmbiguity,
    )
    message = str(error)
    assert str(status_code) in message
    assert "sensitive response body" not in message
    assert "payer@example.invalid" not in message
    assert "11144477735" not in message


def test_create_pix_payment_invalid_json_is_ambiguous(monkeypatch):
    error, _ = _run_create(
        monkeypatch,
        lambda request: httpx.Response(
            200, content=b"{invalid", request=request
        ),
        error_match=mercado_pago.ProviderCreateAmbiguity,
    )
    assert "invalid JSON response" in str(error)


@pytest.mark.parametrize("payload", [{}, {"id": None}, {"id": "  "}, []])
def test_create_pix_payment_without_order_id_is_ambiguous(monkeypatch, payload):
    error, _ = _run_create(
        monkeypatch,
        _json_response(200, payload),
        error_match=mercado_pago.ProviderCreateAmbiguity,
    )
    assert "missing order id" in str(error)


def test_create_pix_payment_client_error_is_not_success_or_ambiguous(monkeypatch):
    error, _ = _run_create(
        monkeypatch,
        lambda request: httpx.Response(
            422,
            text="sensitive response body payer@example.invalid 11144477735",
            request=request,
        ),
        error_match=RuntimeError,
    )
    assert not isinstance(error, mercado_pago.ProviderCreateAmbiguity)
    assert "HTTP 422" in str(error)
    assert "sensitive response body" not in str(error)
