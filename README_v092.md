# FRcaixinha 2.0 — v0.92
## Relatório Executivo de Auditoria

A v0.92 consolida a trilha de melhoria contínua em um relatório administrativo único: recomendações, planos, decisões, execuções, evidências, certificações e snapshots de auditoria.

### Status
- PASS: sem falhas de integridade e sem pendências críticas.
- ATTENTION: há planos vencidos ou ciclos verificados ainda não certificados/auditados.
- CRITICAL: há falha de integridade em um ou mais ciclos.

### Integridade
O `snapshot_hash` é calculado sobre o conteúdo lógico do relatório, excluindo `generated_at`, permitindo comparar o mesmo estado lógico sem que o horário altere o hash.

### API
- `GET /api/admin/continuous-improvement-executive-audit/current`
- `POST /api/admin/continuous-improvement-executive-audit/snapshot`
- `POST /api/admin/continuous-improvement-executive-audit/sync`
- `GET /api/admin/continuous-improvement-executive-audit/history`
- `GET /api/admin/continuous-improvement-executive-audit/snapshots/{id}`
- `GET /api/admin/continuous-improvement-executive-audit/snapshots/{id}/verify`

### Migração
Alembic `0069_continuous_improvement_executive_audit_v092`.
