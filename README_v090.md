# FRcaixinha 2.0 — v0.90

## Fechamento e Certificação da Melhoria

A v0.90 consolida o ciclo de melhoria contínua em um pacote final de auditoria. Uma execução só pode ser certificada quando estiver `VERIFIED`, as evidências físicas forem válidas e a cadeia de integridade de evidências estiver íntegra.

### Entrega
- `ContinuousImprovementCertification` com certificado único por execução.
- Pacote canônico contendo decisão, recomendação, plano, execução, arquivos de evidência e eventos de integridade.
- SHA-256 do pacote final (`package_hash`).
- Certificado com identificador `FRC90-...`.
- Certificador independente do executor e do verificador.
- Certificação irreversível no fluxo: não há edição do certificado; nova certificação para a mesma execução é bloqueada.
- API administrativa para certificar, listar, detalhar e verificar certificados.
- Worker diário tenta certificar execuções `VERIFIED` ainda não certificadas, escolhendo apenas administrador ativo e independente.
- Migração Alembic `0067_continuous_improvement_certification_v090`.

### API
- `POST /api/admin/continuous-improvement-certification/executions/{execution_id}/certify`
- `GET /api/admin/continuous-improvement-certification`
- `GET /api/admin/continuous-improvement-certification/{certificate_id}`
- `GET /api/admin/continuous-improvement-certification/{certificate_id}/verify`

### Regra de certificação
`ACCEPT` → atribuição governada → execução → evidências físicas → verificação independente → **CERTIFIED**.

A certificação não altera automaticamente decisões financeiras e não substitui controles de negócio, reconciliação ou requisitos legais.
