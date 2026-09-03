import httpx
from app.core.config import settings

class MercadoPagoClient:
    def __init__(self):
        self.base_url = settings.mercado_pago_base_url.rstrip("/")
        self.token = settings.mercado_pago_access_token

    def _headers(self, idempotency_key: str):
        if not self.token:
            raise RuntimeError("MERCADO_PAGO_ACCESS_TOKEN não configurado")
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "X-Idempotency-Key": idempotency_key,
        }

    async def create_pix_payment(self, *, amount, email, cpf, description, idempotency_key, external_reference=None):
        payload = {
            "transaction_amount": float(amount),
            "description": description,
            "payment_method_id": "pix",
            **({"external_reference": external_reference} if external_reference else {}),
            "payer": {
                "email": email,
                "identification": {"type": "CPF", "number": cpf},
            },
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self.base_url}/v1/payments",
                json=payload,
                headers=self._headers(idempotency_key),
            )
            response.raise_for_status()
            return response.json()

    async def get_payment(self, payment_id: str):
        if not self.token:
            raise RuntimeError("MERCADO_PAGO_ACCESS_TOKEN não configurado")
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{self.base_url}/v1/payments/{payment_id}",
                headers={"Authorization": f"Bearer {self.token}"},
            )
            response.raise_for_status()
            return response.json()
