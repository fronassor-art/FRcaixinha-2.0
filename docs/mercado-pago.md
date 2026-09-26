# Mercado Pago — Pix

## Integração atual

O backend cria cobranças Pix pela Orders API do Mercado Pago, com `POST /v1/orders`, e envia o cabeçalho `X-Idempotency-Key`. Para consultar o estado canônico de uma cobrança, usa `GET /v1/orders/{order_id}`. O payload do webhook não é suficiente para confirmar o pagamento: após validar o evento, o backend consulta novamente a Order.

O registro local persiste `provider_order_id` e `provider_payment_id`. Quando retornados na criação, também persiste `qr_code`, `qr_code_base64` e `ticket_url`.

## Webhook e confirmação

O endpoint configurado no painel do Mercado Pago é:

`https://SEU-DOMINIO/api/payments/webhook/mercado-pago`

O webhook aceita eventos `payment` e `order`. A validação HMAC-SHA256 usa `x-signature`, `x-request-id` e `data.id`. O timestamp do Mercado Pago pode chegar em segundos ou milissegundos: a conversão é usada para verificar frescor, enquanto o timestamp bruto recebido é preservado na composição do HMAC.

O estado legado `approved` continua sendo aceito para compatibilidade. Na Orders API, `processed` só confirma financeiramente quando `status_detail == accredited`; `processed` sem esse detalhe não cria settlement. Os estados `refunded` e `charged_back` não revertem automaticamente lançamentos financeiros e seguem para reconciliação.

Settlement e Ledger mantêm proteção idempotente para evitar efeitos financeiros duplicados em eventos repetidos ou retentativas.

## Configuração e segurança

No `.env` do backend:

- `MERCADO_PAGO_ACCESS_TOKEN`
- `MERCADO_PAGO_WEBHOOK_SECRET`

Credenciais do Mercado Pago pertencem somente ao backend. Nunca exponha o Access Token ou o segredo do webhook no Flutter, no aplicativo Android ou no repositório.

Validação automatizada e CI não substituem um teste E2E real em staging com credenciais e eventos do Mercado Pago.

## Checkpoint validado

- SHA: `221226b3f9b0c93517721398de3cc85b2d565a2a`
- Backend CI: 1592 passed, 16 skipped, 145 warnings
- Backend CI, PostgreSQL payout verification, Android APK, Android Release e Pages: success
