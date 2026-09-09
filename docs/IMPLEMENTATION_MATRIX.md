# FRcaixinha 1.0 — Matriz de Implementação

Esta matriz liga cada regra técnica do catálogo ao módulo responsável,
às estruturas de dados, às APIs e aos testes necessários.

Nenhuma regra financeira será considerada implementada apenas por existir
no código. Ela deverá possuir teste e evidência de homologação.

---

## 1. IDENTIDADE E ACESSO

| ID | Módulo | Estrutura principal | API/Serviço | Teste mínimo |
|---|---|---|---|---|
| AUTH-001 | Identity | users, user_profiles | /api/v1/auth | identidade única |
| AUTH-002 | Identity | users, sessions | /api/v1/auth | autenticação válida/inválida |

## 2. MEMBROS

| ID | Módulo | Estrutura principal | API/Serviço | Teste mínimo |
|---|---|---|---|---|
| MEM-001 | Membership | memberships, cycles | /api/v1/memberships | associação ao ciclo |
| MEM-002 | Membership | invitations | /api/v1/invitations | convite obrigatório |
| MEM-003 | Membership | contract_acceptances | /api/v1/contracts | aceite obrigatório |
| MEM-004 | Membership/Payments | memberships, payments | /api/v1/memberships | pagamento inicial e ativação |

## 3. FINANCEIRO

| ID | Módulo | Estrutura principal | API/Serviço | Teste mínimo |
|---|---|---|---|---|
| FIN-001 | Financial Calendar | cycle_policies | FinancialCalendar | vencimento |
| FIN-002 | Financial Calendar | holidays | FinancialCalendar | dia útil/feriado |
| FIN-003 | Contributions | contributions | /api/v1/contributions | valor do ciclo |
| FIN-004 | Contributions | contributions, payments | ContributionService | pagamento confirmado |
| FIN-005 | Delinquency | contributions, payments | RuleEngine | inadimplência |
| FIN-010 | Financial Engine | contributions, ledger_entries | FinancialEngine | resultado realizado |
| FIN-011 | Administration | ledger_entries | ClosingEngine | taxa administrativa |
| FIN-012 | Financial Separation | cycles, ledger_accounts | LedgerService | separação por ciclo |

## 4. REGRAS FINANCEIRAS DE REFERÊNCIA

| ID | Módulo | Estrutura principal | API/Serviço | Teste mínimo |
|---|---|---|---|---|
| FIN-REF-001 | Delinquency | contributions | RuleEngine | multa fixa R$10 |
| FIN-REF-002 | Delinquency | contributions | RuleEngine | encargo de 10% após 30 dias |
| FIN-REF-003 | Contribution Adjustment | contributions | FinancialCalendar | vencimento ajustado |
| FIN-REF-004 | Cycle Policy | cycle_policies | PolicyService | regra congelada no ciclo |

## 5. NOTIFICAÇÕES

| ID | Módulo | Estrutura principal | API/Serviço | Teste mínimo |
|---|---|---|---|---|
| NOTIF-001 | Notifications | notifications | NotificationService | envio de aviso |
| NOTIF-002 | Notifications | notifications, job_executions | NotificationJob | repetição a cada 2 dias |

Canais previstos:
- Email
- Push
- WhatsApp

Falha de um canal não deve apagar o registro da obrigação de notificação.

## 6. LEDGER

| ID | Módulo | Estrutura principal | API/Serviço | Teste mínimo |
|---|---|---|---|---|
| LEDGER-001 | Ledger | ledger_transactions, ledger_entries | LedgerService | lançamento imutável |
| LEDGER-002 | Ledger | ledger_entries | LedgerService | débito/crédito balanceado |
| LEDGER-003 | Ledger | ledger_entries | LedgerService | estorno por reversão |
| LEDGER-004 | Ledger | ledger_entries | LedgerService | rastreabilidade por ciclo |

Regra estrutural:

Nenhum lançamento financeiro confirmado poderá ser apagado ou alterado
diretamente.

Correção deverá gerar operação de reversão e novo lançamento correto.

## 7. PAGAMENTOS

| ID | Módulo | Estrutura principal | API/Serviço | Teste mínimo |
|---|---|---|---|---|
| PAY-001 | Payments/Reconciliation | payments, payment_events | PaymentService | confirmação e estorno |
| PAY-002 | Contributions/Ledger | contributions, ledger_entries | ReconciliationService | participação somente do confirmado |

Integração prevista:

Mercado Pago Sandbox → PaymentService → Reconciliation → Ledger.

## 8. DISTRIBUIÇÃO

| ID | Módulo | Estrutura principal | API/Serviço | Teste mínimo |
|---|---|---|---|---|
| DIST-001 | Distribution | profit_distributions | DistributionEngine | resultado distribuível |
| DIST-002 | Distribution | distribution_items | DistributionEngine | cálculo individual |
| DIST-003 | Distribution | contributions, distribution_items | CapitalDaysEngine | capital-dias |
| DIST-004 | Distribution | distribution_items | DistributionEngine | descontos/obrigações |
| DIST-005 | Distribution | financial_snapshots | DistributionEngine | resultado final individual |

### Fórmula obrigatória

Para cada contribuição confirmada:

`capital_dias = valor_confirmado × dias_elegíveis`

Para cada participante:

`participação = soma(capital_dias)`

Resultado individual:

`resultado_individual =
resultado_distribuível × participação_individual / participação_total`

A convenção exata de inclusão/exclusão do dia inicial e final deverá ser
definida antes da implementação definitiva do CapitalDaysEngine.

## 9. RESULTADO E SEPARAÇÃO FINANCEIRA

| ID | Módulo | Estrutura principal | API/Serviço | Teste mínimo |
|---|---|---|---|---|
| FIN-010 | Financial Engine | ledger_entries, financial_snapshots | FinancialEngine | somente resultado realizado |
| FIN-011 | Closing | financial_snapshots | ClosingEngine | taxa administrativa |
| FIN-012 | Cycle | cycles, ledger_accounts | CycleEngine | isolamento entre ciclos |

Nunca considerar recebível futuro como caixa disponível ou lucro realizado.

## 10. CRÉDITO PADRÃO

| ID | Módulo | Estrutura principal | API/Serviço | Teste mínimo |
|---|---|---|---|---|
| LOAN-001 | Loan Engine | loans, loan_installments | LoanEngine | juros sobre saldo |
| LOAN-002 | Loan Engine | loan_installments | AmortizationEngine | amortização |
| LOAN-003 | Loan Engine | loan_installments | AmortizationEngine | parcelas fixas |
| LOAN-004 | Loan Engine | loan_installments | AmortizationEngine | ajuste final |
| LOAN-005 | Loan Engine | loans, loan_installments | LoanSettlementService | quitação antecipada |

### Regra de juros

Taxa:

`20% ao mês`

Base:

`saldo devedor anterior`

Juros:

`saldo_anterior × 0,20`

Amortização:

`parcela − juros`

Novo saldo:

`saldo_anterior − amortização`

O cálculo financeiro deverá utilizar Decimal/Numeric, nunca float/double.

A última parcela poderá sofrer ajuste de centavos para garantir saldo final
exatamente igual a zero.

## 11. INADIMPLÊNCIA DO EMPRÉSTIMO

| ID | Módulo | Estrutura principal | API/Serviço | Teste mínimo |
|---|---|---|---|---|
| LOAN-010 | Loan Delinquency | loan_installments | RuleEngine | multa fixa R$10 |
| LOAN-011 | Loan Delinquency | loan_installments | RuleEngine | 10% após 30 dias |

O encargo percentual deverá incidir sobre o principal da parcela vencida
conforme a regra aprovada, sem capitalização sucessiva do próprio encargo.

## 12. GARANTIA

| ID | Módulo | Estrutura principal | API/Serviço | Teste mínimo |
|---|---|---|---|---|
| GAR-001 | Guarantees | loan_guarantees | GuaranteeService | garantia de joia |
| GAR-002 | Guarantees | loan_guarantees | GuaranteeService | 80% do valor avaliado |
| GAR-003 | Guarantees | guarantee_documents | GuaranteeService | registro digital |

A garantia de joia deverá registrar avaliação/documentação, valor avaliado,
valor considerado como garantia, fotos, identificação, custódia e status.

Não implementar regras jurídicas de execução/liberação além das regras
expressamente homologadas.

## 13. FECHAMENTO

| ID | Módulo | Estrutura principal | API/Serviço | Teste mínimo |
|---|---|---|---|---|
| CLOSE-001 | Closing | cycles, financial_snapshots | ClosingEngine | fechamento por ciclo |
| CLOSE-002 | Snapshot | financial_snapshots | SnapshotService | snapshot imutável |
| CLOSE-003 | Post Closing | audit_logs, ledger_entries | CorrectionService | correção rastreável |
| CLOSE-004 | Receivables | receivables | ClosingEngine | empréstimo aberto |
| CLOSE-005 | Financial Separation | financial_snapshots, ledger_entries | ClosingEngine | recebível ≠ caixa |

### Fechamento com empréstimo aberto

O empréstimo permanece vinculado ao ciclo original.

O fechamento operacional do ciclo não transfere a dívida para o ciclo seguinte.

O principal em aberto permanece como recebível do ciclo original.

Não será considerado dinheiro em caixa nem resultado realizado.

## 14. AUDITORIA

| ID | Módulo | Estrutura principal | API/Serviço | Teste mínimo |
|---|---|---|---|---|
| AUDIT-001 | Audit | audit_logs | AuditService | operação crítica rastreável |
| AUDIT-002 | Audit | audit_logs | AuditService | correção rastreável |
| AUDIT-003 | Security/Audit | audit_logs | AuditService | proteção contra alteração |

Operações críticas deverão registrar, no mínimo:

- usuário/ator;
- ação;
- data/hora;
- entidade;
- identificador da operação;
- resultado;
- referência financeira quando aplicável.

## 15. IDEMPOTÊNCIA

| ID | Módulo | Estrutura principal | API/Serviço | Teste mínimo |
|---|---|---|---|---|
| IDEMP-001 | Core Financial | idempotency_keys | IdempotencyService | repetição não duplica |
| IDEMP-002 | Payments | payment_events | PaymentService | identificador externo único |

Operações financeiras deverão possuir proteção contra:

- duplo clique;
- retry;
- webhook duplicado;
- repetição de requisição;
- reprocessamento de job.

## 16. DEFINITION OF DONE

| ID | Módulo | Critério | Teste |
|---|---|---|---|
| TEST-001 | QA | regra implementada e validada | unitário + integração + financeiro + segurança + homologação |

Uma regra somente poderá ser marcada como concluída quando possuir:

1. definição objetiva;
2. implementação;
3. teste unitário;
4. teste de integração quando aplicável;
5. teste financeiro quando aplicável;
6. teste de segurança quando aplicável;
7. homologação.

---

# 17. MATRIZ DE DEPENDÊNCIAS

A ordem mínima de implementação deverá respeitar:

```text
IDENTIDADE
    ↓
MEMBROS
    ↓
CICLOS / CONTRATOS
    ↓
CALENDÁRIO FINANCEIRO
    ↓
CONTRIBUIÇÕES
    ↓
PAGAMENTOS
    ↓
RECONCILIAÇÃO
    ↓
LEDGER
    ↓
MOTOR FINANCEIRO
    ↓
CRÉDITO
    ↓
GARANTIAS
    ↓
FECHAMENTO
    ↓
DISTRIBUIÇÃO
    ↓
NOTIFICAÇÕES
    ↓
AUDITORIA
    ↓
ADMIN
    ↓
FLUTTER
