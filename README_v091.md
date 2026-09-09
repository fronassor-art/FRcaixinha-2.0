# FRcaixinha 2.0 — v0.91

## Auditoria e consulta do ciclo completo de melhoria

A v0.91 consolida em uma visão auditável o ciclo Recomendação → Distribuição → Decisão → Plano → Execução → Evidências → Integridade → Certificação.

### Controles
- snapshot do ciclo completo com SHA-256;
- validação independente dos hashes e da cadeia de evidências;
- consulta atual por execução;
- histórico de snapshots;
- verificação de snapshot armazenado versus ciclo atual;
- AuditLog para criação de snapshots.

### API
`GET /api/admin/continuous-improvement-audit/executions/{execution_id}`
`POST /api/admin/continuous-improvement-audit/executions/{execution_id}/snapshot`
`POST /api/admin/continuous-improvement-audit/executions/{execution_id}/verify`
`GET /api/admin/continuous-improvement-audit/snapshots`
`GET /api/admin/continuous-improvement-audit/snapshots/{snapshot_id}`
`GET /api/admin/continuous-improvement-audit/snapshots/{snapshot_id}/verify`

Alembic HEAD: `0068`.
