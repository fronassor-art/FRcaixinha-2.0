# FRcaixinha 2.0 v0.85 — Motor de Priorização de Melhorias

A v0.85 cria uma fila executiva determinística e explicável para priorizar recomendações de melhoria contínua. O score combina risco operacional, padrão/impacto, urgência/SLA, recorrência/amostra e histórico de inefetividade.

## Segurança
Somente recomendação e priorização. Não aprova empréstimos, libera recursos, altera pagamentos, saldos, Ledger, limites ou políticas financeiras.

## API
- GET /api/admin/continuous-improvement-priority
- POST /api/admin/continuous-improvement-priority/snapshot
- POST /api/admin/continuous-improvement-priority/sync
- GET /api/admin/continuous-improvement-priority/history
- GET /api/admin/continuous-improvement-priority/snapshot/{id}

## Migration
0062_continuous_improvement_priority_v085
