# Mercado Pago — Pix

A documentação oficial do Mercado Pago indica o uso de `POST /v1/payments` para Pix e exige `X-Idempotency-Key` para evitar execução duplicada. O backend usa UUID por cobrança.

Os Webhooks de pagamento devem ser configurados no painel do Mercado Pago. O backend valida `x-signature` por HMAC-SHA256 usando o segredo do webhook e também registra o ID do evento para impedir processamento repetido.

## Configuração

No `.env`:
- `MERCADO_PAGO_ACCESS_TOKEN`
- `MERCADO_PAGO_WEBHOOK_SECRET`

URL do webhook:
`https://SEU-DOMINIO/api/payments/webhook/mercado-pago`

Use credenciais de teste primeiro. Nunca coloque o Access Token no aplicativo Android ou no repositório.
