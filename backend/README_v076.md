# FRcaixinha 2.0 v0.76 — Orquestração de Alertas e Planos de Resposta

Conecta alertas preditivos v0.75 a um plano operacional com responsável, SLA, tarefa de workflow, evidência e verificação humana. Não executa decisões financeiras automaticamente.

## Fluxo
Alerta → plano → tarefa → responsável → execução/evidência → verificação → resolução.

## API
- POST /api/admin/operational-risk-response/sync
- GET /api/admin/operational-risk-response
- POST /api/admin/operational-risk-response/{id}/assign
- POST /api/admin/operational-risk-response/{id}/verify

Migration HEAD: 0053_operational_risk_response_v076.
