import httpx
from app.core.config import settings


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
        payload = {
            "type": "online",
            "total_amount": f"{float(amount):.2f}",
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
                        "amount": f"{float(amount):.2f}",
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
            },
        }

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self.base_url}/v1/orders",
                json=payload,
                headers=self._headers(idempotency_key),
            )

            if response.is_error:
                raise RuntimeError(
                    f"Mercado Pago {response.status_code}: {response.text}"
                )

            order = response.json()

        payments = ((order.get("transactions") or {}).get("payments") or [])
        payment = payments[0] if payments else {}
        payment_method = payment.get("payment_method") or {}

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

    async def get_payment(self, payment_id: str):
        if not self.token:
            raise RuntimeError("MERCADO_PAGO_ACCESS_TOKEN não configurado")

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{self.base_url}/v1/payments/{payment_id}",
                headers={
                    "Authorization": f"Bearer {self.token}",
                },
            )

            response.raise_for_status()
            return response.json()
