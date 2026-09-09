# FRcaixinha v0.5 — Contribuição + Pix

Implementado:
- Lista e resumo de contribuições do participante.
- Geração de Pix pelo backend.
- Reutilização de cobrança Pix pendente para evitar duplicidade.
- QR Code/copia e cola no aplicativo.
- Consulta de status e atualização automática a cada 5 segundos enquanto pendente.
- Webhook Mercado Pago como fonte de confirmação.
- Registro de pagamento aprovado no ledger, protegido contra duplicação.
- Endpoints protegidos pelo JWT.

O Access Token do Mercado Pago permanece apenas no servidor. O Mercado Pago exige `X-Idempotency-Key` nas chamadas de criação de pagamento; a documentação oficial também orienta a validação da assinatura dos Webhooks.
