# FRcaixinha 1.0 — Arquitetura Oficial do Backend

## 1. Objetivo

O backend do FRcaixinha 1.0 será a autoridade central das regras financeiras, de segurança, ciclo, crédito, pagamentos, Ledger, fechamento, distribuição e auditoria.

O aplicativo Flutter e o painel administrativo não poderão substituir ou contornar as validações realizadas pelo backend.

Arquitetura principal:

```text
Flutter Mobile
      |
      v
    HTTPS
      |
      v
FastAPI /api/v1
      |
      +-------------------+
      |                   |
      v                   v
Rule Engine          Auth / RBAC
      |
      v
Financial Services
      |
      +----------+----------+----------+----------+
      |          |          |          |          |
      v          v          v          v          v
Contrib.    Payments     Loans      Ledger     Closing
      |          |          |          |          |
      +----------+----------+----------+----------+
                              |
                              v
                         PostgreSQL
