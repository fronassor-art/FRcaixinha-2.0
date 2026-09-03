# FRcaixinha 2.0 — v0.89

## Evidências reais e integridade da execução

A v0.89 evolui a execução controlada da v0.88 para evidências físicas anexadas à execução.

### Entregas
- upload privado de evidências por execução;
- nomes sanitizados, extensão/MIME permitidos e limite de tamanho;
- armazenamento fora da área pública com chave UUID;
- SHA-256 calculado durante o upload;
- manifesto determinístico dos arquivos e `evidence_manifest_hash` na execução;
- acesso/download auditado;
- verificação física dos arquivos e detecção de `MISMATCH`, `MISSING` e `REVOKED`;
- cadeia hash de eventos de integridade;
- execução só pode ser verificada quando houver evidência válida;
- nova migração `0066_continuous_improvement_evidence_v089`;
- API `/api/admin/continuous-improvement-evidence`.

A v0.89 não altera automaticamente responsáveis nem decisões de negócio. A evidência complementa a governança existente e mantém a verificação independente.

## Validação
`compileall`: OK
Testes v0.86–v0.89: 9 passed
Alembic HEAD: `0066_continuous_improvement_evidence_v089`
