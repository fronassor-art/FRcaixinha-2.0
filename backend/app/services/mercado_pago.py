import httpx
from decimal import Decimal, ROUND_HALF_UP
from app.core.config import settings


CENT = Decimal("0.01")


class ProviderCreateAmbiguity(RuntimeError):
    """The create request may have produced a provider-side effect."""


def serialize_provider_money(amount: Decimal) -> str:
    if not isinstance(amount, Decimal):
        raise TypeError("provider amount must be Decimal")
    if not amount.is_finite() or amount < 0:
        raise ValueError("provider amount must be a finite non-negative Decimal")
    quantized = amount.quantize(CENT, rounding=ROUND_HALF_UP)
    if quantized != amount:
        raise ValueError("provider amount must be exactly representable in cents")
    return format(quantized, ".2f")


class MercadoPagoClient:
    def __init__(self):
        self.base_url = settings.mercado_pago_base_url.rstrip("/")
        self.token = settings.mercado_pago_access_token

    def _headers(self, idempotency_key: str | None = None):
        if not self.token:
            raise RuntimeError("MERCADO_PAGO_ACCESS_TOKEN não configurado")

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key

        return headers

    async def create_pix_payment(
        self,
        *,
        amount,
        email,
        cpf,
        description,
        idempotency_key,
        external_reference=None,
    ):
        money = serialize_provider_money(amount)
        payload = {
            "type": "online",
            "total_amount": money,
            "processing_mode": "automatic",
            "capture_mode": "automatic_async",
            **(
                {"external_reference": external_reference}
                if external_reference
                else {}
            ),
            "transactions": {
                "payments": [
                    {
                        "amount": money,
                        "payment_method": {
                            "id": "pix",
                            "type": "bank_transfer",
                        },
                    }
                ]
            },
            "payer": {
                "email": (
                    settings.mercado_pago_sandbox_email
                    if settings.app_env == "development"
                    and settings.mercado_pago_sandbox_email
                    else email
                ),
                **({"first_name": "APRO"} if settings.app_env == "development" else {}),
            },
        }

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    f"{self.base_url}/v1/orders",
                    json=payload,
                    headers=self._headers(idempotency_key),
                )
        except httpx.RequestError:
            raise ProviderCreateAmbiguity(
                "Mercado Pago Orders create ambiguous: transport failure"
            ) from None

        if response.status_code >= 500:
            raise ProviderCreateAmbiguity(
                "Mercado Pago Orders create ambiguous: "
                f"HTTP {response.status_code}"
            )
        if 300 <= response.status_code < 400:
            raise ProviderCreateAmbiguity(
                "Mercado Pago Orders create ambiguous: "
                f"HTTP {response.status_code} redirect response"
            )
        if not response.is_success:
            raise RuntimeError(
                "Mercado Pago Orders create returned HTTP response: "
                f"HTTP {response.status_code}"
            )

        try:
            order = response.json()
        except ValueError:
            raise ProviderCreateAmbiguity(
                "Mercado Pago Orders create ambiguous: invalid JSON response"
            ) from None

        if (
            not isinstance(order, dict)
            or order.get("id") is None
            or not str(order.get("id")).strip()
        ):
            raise ProviderCreateAmbiguity(
                "Mercado Pago Orders create ambiguous: missing order id"
            )

        transactions = order.get("transactions")
        if "transactions" in order and not isinstance(transactions, dict):
            raise ProviderCreateAmbiguity(
                "Mercado Pago Orders create ambiguous: invalid transactions"
            )

        payments = transactions.get("payments") if transactions is not None else None
        if transactions is not None and "payments" in transactions:
            if not isinstance(payments, list):
                raise ProviderCreateAmbiguity(
                    "Mercado Pago Orders create ambiguous: invalid payments"
                )
        else:
            payments = []

        if payments and not isinstance(payments[0], dict):
            raise ProviderCreateAmbiguity(
                "Mercado Pago Orders create ambiguous: invalid payment"
            )
        payment = payments[0] if payments else {}
        payment_method = payment.get("payment_method")
        if "payment_method" in payment and not isinstance(payment_method, dict):
            raise ProviderCreateAmbiguity(
                "Mercado Pago Orders create ambiguous: invalid payment method"
            )
        payment_method = payment_method or {}

        payment_id = payment.get("id") or order.get("id")

        return {
            "id": payment_id,
            "order_id": order.get("id"),
            "status": payment.get("status") or order.get("status") or "PENDING",
            "qr_code": payment_method.get("qr_code"),
            "qr_code_base64": payment_method.get("qr_code_base64"),
            "ticket_url": payment_method.get("ticket_url"),
            "raw": order,
        }

    async def get_order(self, order_id: str):
        if not self.token:
            raise RuntimeError("MERCADO_PAGO_ACCESS_TOKEN não configurado")

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{self.base_url}/v1/orders/{order_id}",
                headers={
                    "Authorization": f"Bearer {self.token}",
                },
            )

        response.raise_for_status()
        return response.json()
