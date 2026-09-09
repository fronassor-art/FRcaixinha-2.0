# FRcaixinha 1.0

Sistema privado de gestão financeira para grupo fechado.

## Estado atual

Fase de preparação técnica e especificação.

A implementação financeira somente deve começar após a validação da especificação master, regras de negócio, arquitetura, banco de dados, Ledger, segurança, compatibilidade e estratégia de testes.

## Estrutura

- `.github/` — CI/CD
- `admin/` — painel administrativo
- `backend/` — API e regras de negócio
- `docs/` — documentação
- `infra/` — infraestrutura
- `mobile/` — aplicativo Flutter
- `scripts/` — automações e ferramentas
- `tests/` — testes automatizados

## Princípios

1. Backend é a autoridade das regras financeiras.
2. Operações financeiras são registradas em Ledger imutável.
3. Correções financeiras usam reversão, nunca exclusão silenciosa.
4. Valores monetários usam Decimal/Numeric, nunca float/double.
5. Toda regra financeira deve possuir testes.
6. Fechamentos devem possuir reconciliação e snapshot.
7. Ciclos financeiros são independentes.
8. O aplicativo não pode contornar regras de segurança ou aprovação.
9. Segredos e credenciais nunca entram no Git.
10. O sistema deve bloquear operações incompatíveis antes da execução.

## Ciclo inicial

Ciclo 001:

- Início: 10/12/2026
- Encerramento: 09/12/2027 23:59:59
- Contribuição nominal: R$150,00/mês
- Máximo inicial: 50 quotas

## Crédito

Empréstimos padrão e créditos especiais/extraordinários possuem análise de elegibilidade, liquidez, exposição, risco e aprovação conforme a política do ciclo.

A taxa aprovada para os empréstimos é de 20% ao mês sobre o saldo devedor, com amortização pelo sistema Price e ajuste final de centavos.

## Desenvolvimento

Sequência obrigatória:

Contrato
→ Especificação Master
→ Regras
→ Banco de Dados
→ Ledger
→ Motor Financeiro
→ API
→ Pagamentos
→ Segurança
→ Flutter
→ Administração
→ Testes
→ CI/CD
→ APK

## Definition of Done

Uma regra somente é considerada implementada quando possuir:

- especificação;
- implementação;
- teste unitário;
- teste de integração;
- teste financeiro/segurança quando aplicável;
- homologação.
