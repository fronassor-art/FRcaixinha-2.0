# FRcaixinha 2.0 — v0.40

## Fechamento contábil e conciliação avançada

A v0.40 adiciona uma camada de reconciliação financeira antes do fechamento mensal.

### Entregas
- `FinancialReconciliation` com snapshot JSON e SHA-256.
- Conciliação operacional x Ledger para contribuições, despesas, recebimentos de empréstimos e acordos.
- Verificação de pagamentos aprovados ainda não contabilizados.
- Verificação de Webhooks pendentes.
- Verificação de saldos negativos de parcelas.
- Exposição operacional aberta de empréstimos e acordos no snapshot.
- Histórico das execuções de conciliação.
- Fechamento mensal passa a exigir conciliação avançada `PASS`.
- Verificação do fechamento compara o snapshot de reconciliação.
- Alembic HEAD: `0018_financial_reconciliation_v040`.

## Endpoints
- `GET /api/admin/reconciliation/advanced?competence=YYYY-MM-DD`
- `POST /api/admin/reconciliation/advanced/run?competence=YYYY-MM-DD`
- `GET /api/admin/reconciliation/advanced/history`

## Segurança financeira
Nenhum lançamento existente é apagado ou alterado para corrigir divergências. Divergências devem ser tratadas por correção operacional/auditável e, quando aplicável, reversão no Ledger.

## Mercado Pago
O fluxo continua baseado em Webhooks autenticados e processamento idempotente; o Mercado Pago recomenda Webhooks e informa que notificações podem ser reenviadas quando a confirmação não é recebida.
