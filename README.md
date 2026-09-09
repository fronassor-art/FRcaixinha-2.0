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

---

## 27. BANCO DE DADOS

O banco de dados deverá utilizar PostgreSQL.

Todas as entidades financeiras deverão possuir vínculo explícito com o ciclo financeiro correspondente quando aplicável.

Nenhuma operação financeira poderá depender exclusivamente de dados calculados no aplicativo Flutter.

O backend será a autoridade para validação, cálculo, persistência e alteração de estados financeiros.

### 27.1 Identidade e acesso

Tabelas principais:

- users
- user_profiles
- addresses
- roles
- permissions
- user_roles
- invitations
- sessions

Responsabilidades:

- identificação do usuário;
- dados cadastrais;
- endereço completo;
- controle de papéis;
- permissões;
- convites;
- sessões e dispositivos;
- controle de acesso administrativo.

CPF, e-mail e telefone deverão possuir regras de unicidade e validação compatíveis com o contrato.

---

### 27.2 Contratos e ciclos

Tabelas:

- contracts
- contract_versions
- contract_acceptances
- cycles
- cycle_policies
- memberships

Cada ciclo deverá possuir:

- identificador interno;
- número do ciclo;
- contrato/versionamento aplicável;
- data inicial;
- data final;
- status;
- valor da contribuição;
- regras financeiras;
- regras de crédito;
- regras de inadimplência;
- políticas de liquidez;
- políticas de risco;
- parâmetros de encerramento.

O valor da contribuição pertence ao ciclo.

Após a homologação/abertura financeira do ciclo, seus parâmetros financeiros não poderão ser alterados retroativamente.

Cada participação do usuário deverá estar vinculada ao ciclo correspondente.

Não poderá existir transferência silenciosa de saldo entre ciclos.

---

### 27.3 Contribuições e pagamentos

Tabelas:

- contributions
- payments
- payment_events
- payment_reconciliation

Uma contribuição deverá registrar, quando aplicável:

- usuário;
- ciclo;
- competência;
- valor devido;
- valor confirmado;
- vencimento original;
- vencimento ajustado;
- data de pagamento;
- status;
- referência externa;
- identificador de idempotência.

Estados mínimos de pagamento:

CREATED
→ PENDING
→ PROCESSING
→ APPROVED
→ CONFIRMED

Também deverão existir estados controlados para:

- rejeição;
- cancelamento;
- estorno;
- erro;
- expiração.

Pagamentos parcialmente realizados deverão permitir o registro do valor efetivamente confirmado.

Somente valores financeiramente confirmados poderão participar do cálculo de participação.

---

### 27.4 Ledger

Tabelas:

- ledger_accounts
- ledger_transactions
- ledger_entries

O Ledger será a fonte contábil interna das movimentações financeiras do sistema.

O Ledger deverá ser:

- imutável;
- auditável;
- vinculado ao ciclo;
- rastreável até a origem da operação;
- compatível com partidas de débito e crédito.

Nenhuma movimentação financeira confirmada deverá ser apagada.

Correções deverão utilizar:

1. lançamento de reversão;
2. novo lançamento correto.

O estorno deverá retirar do cálculo de participação somente o valor que deixou de permanecer financeiramente disponível.

---

### 27.5 Crédito e empréstimos

Tabelas:

- loans
- loan_installments
- loan_payments
- loan_risk_assessments
- loan_approvals
- loan_guarantees
- guarantee_documents

Todo empréstimo deverá estar vinculado ao ciclo de origem.

O empréstimo padrão utilizará:

- juros de 20% ao mês;
- saldo devedor decrescente;
- amortização pelo sistema Price;
- prazo de 1 a 6 parcelas;
- ajuste da última parcela para zerar exatamente o saldo.

O cálculo financeiro deverá utilizar Decimal/Numeric, nunca float/double.

A quitação antecipada não deverá cobrar juros futuros não constituídos.

---

### 27.6 Garantias

Garantias de joias deverão possuir registro digital próprio.

O registro deverá conter, quando aplicável:

- identificador da garantia;
- usuário;
- empréstimo;
- descrição;
- fotografias;
- documento de avaliação;
- valor avaliado;
- valor considerado como garantia;
- data da avaliação;
- termos de custódia;
- status.

Para joias, a avaliação de referência será realizada na Caixa Econômica Federal.

Será considerado como valor de garantia 80% do valor avaliado.

A custódia física será registrada conforme as regras contratuais e legais aplicáveis.

Regras de execução, liberação ou alienação da garantia não deverão ser inventadas pelo software e deverão permanecer subordinadas à validação jurídica.

---

### 27.7 Fechamento e distribuição

Tabelas:

- receivables
- profit_distributions
- distribution_items
- financial_snapshots

O fechamento deverá separar claramente:

- caixa disponível;
- valores realizados;
- valores a receber;
- empréstimos em aberto;
- resultado financeiro;
- valores distribuíveis.

Empréstimo em aberto será tratado como recebível/ativo financeiro e não como receita realizada.

O fechamento de um ciclo não deverá migrar automaticamente esse recebível para o ciclo seguinte.

---

### 27.8 Eventos e benefícios

Tabelas:

- events
- event_benefits
- event_participants

O sistema não possuirá módulo de sorteios, rifas ou loterias.

Eventos poderão existir para:

- relacionamento;
- comunicação;
- benefícios;
- reuniões;
- ações institucionais.

Eventos não deverão alterar automaticamente o resultado financeiro.

Benefícios deverão utilizar critérios objetivos previamente definidos.

---

### 27.9 Notificações e execução de tarefas

Tabelas:

- notifications
- job_executions

As notificações poderão utilizar:

- e-mail;
- push;
- WhatsApp.

Para inadimplência, o sistema deverá permitir avisos periódicos a cada 2 dias, contendo no mínimo:

- contribuição vencida;
- dias de atraso;
- valor atualizado;
- encargos aplicáveis;
- instruções para regularização;
- Pix ou canal oficial de pagamento.

Jobs deverão possuir controle de:

- identificador de execução;
- status;
- início;
- término;
- tentativas;
- erros;
- bloqueio/lock quando necessário.

---

### 27.10 Auditoria e administração

Tabelas:

- audit_logs
- admin_actions
- system_settings

Operações administrativas relevantes deverão ser auditáveis.

O registro de auditoria deverá permitir identificar:

- quem executou;
- o que foi executado;
- quando;
- entidade afetada;
- estado anterior quando aplicável;
- estado posterior quando aplicável;
- origem da operação;
- identificador de correlação.

Alterações críticas deverão possuir proteção adicional e, quando exigido pelas regras, segunda aprovação.

---

### 27.11 Integridade financeira

As principais entidades financeiras deverão possuir:

- identificador único;
- timestamps;
- referência ao ciclo;
- status controlado;
- idempotência quando aplicável;
- referência externa quando aplicável;
- trilha de auditoria.

Não será permitido:

- apagar lançamento financeiro confirmado;
- alterar silenciosamente valores confirmados;
- duplicar pagamento;
- duplicar participação;
- associar pagamento ao ciclo errado;
- transferir saldo entre ciclos sem operação formal;
- distribuir valor não realizado;
- considerar contribuição não paga como capital efetivo.

---

### 27.12 Idempotência

Toda operação financeira crítica deverá ser protegida contra duplicidade.

Deverão existir, conforme o caso:

- idempotency_key;
- external_transaction_id;
- webhook_id;
- processed_at;
- processing_status.

Repetição da mesma requisição não poderá gerar uma segunda movimentação financeira.

---

### 27.13 Migrações

O banco deverá utilizar Alembic para versionamento das estruturas.

As migrações deverão ser incrementais e auditáveis.

Estrutura planejada:

001_initial
002_identity
003_contracts_cycles
004_memberships
005_contributions
006_payments
007_ledger
008_loans
009_guarantees
010_closing_distribution
011_events_notifications
012_audit
013_constraints_indexes
014_idempotency_reconciliation
015_financial_integrity
016_final_constraints
017_test_support

A numeração poderá ser ajustada durante a implementação, desde que a sequência final permaneça determinística e reproduzível.

---

### 27.14 Regra de encerramento do banco

O modelo de dados somente será considerado aprovado quando possuir:

- modelo relacional definido;
- chaves primárias;
- chaves estrangeiras;
- índices;
- restrições de unicidade;
- restrições financeiras;
- estados definidos;
- migrações;
- dados sintéticos de teste;
- testes de integridade;
- testes de concorrência quando aplicável;
- teste de restauração;
- homologação.

Nenhuma implementação financeira definitiva deverá ser considerada concluída sem esses critérios.


---

## 28. LEDGER E INTEGRIDADE FINANCEIRA

O Ledger será o registro interno e imutável das movimentações financeiras do FRcaixinha.

Nenhum saldo financeiro crítico deverá ser obtido apenas pela soma de valores exibidos no aplicativo.

O saldo deverá ser derivado de registros financeiros confirmados e reconciliados.

### 28.1 Princípios

O Ledger deverá obedecer aos seguintes princípios:

- imutabilidade;
- rastreabilidade;
- idempotência;
- consistência;
- atomicidade;
- auditabilidade;
- vínculo obrigatório ao ciclo quando aplicável;
- separação entre valor previsto e valor realizado.

Uma movimentação financeira confirmada não poderá ser apagada ou alterada diretamente.

---

### 28.2 Estrutura

O Ledger utilizará, no mínimo:

- ledger_accounts;
- ledger_transactions;
- ledger_entries.

Uma `ledger_transaction` representará a operação financeira.

Uma ou mais `ledger_entries` representarão os lançamentos da operação.

Cada transação deverá possuir identificador único.

Cada lançamento deverá possuir:

- identificador;
- transação;
- conta;
- ciclo;
- valor;
- natureza;
- data/hora;
- referência da operação de origem;
- status quando aplicável;
- metadados de auditoria.

---

### 28.3 Partidas

O Ledger deverá suportar partidas de débito e crédito.

Para cada transação financeira confirmada:

**Total dos débitos = Total dos créditos**

A regra deverá ser validada pelo backend antes da confirmação da transação.

Uma transação desequilibrada deverá ser rejeitada.

Não será permitido confirmar parcialmente uma transação contábil desequilibrada.

---

### 28.4 Tipos de movimentação

O Ledger deverá permitir identificar, no mínimo:

- contribuição;
- confirmação de pagamento;
- estorno;
- multa;
- penalidade percentual;
- juros de empréstimo;
- amortização de principal;
- liberação de empréstimo;
- pagamento de empréstimo;
- despesa válida;
- taxa de administração;
- resultado financeiro;
- distribuição;
- ajuste formal;
- transferência interna formalmente autorizada.

Cada tipo deverá possuir regras próprias de débito e crédito.

Não será permitido criar lançamentos financeiros genéricos para contornar as regras do motor financeiro.

---

### 28.5 Operação financeira e Ledger

Uma operação financeira deverá seguir o fluxo:

**Solicitação**
→ **Validação**
→ **Transação financeira**
→ **Lançamentos**
→ **Validação de equilíbrio**
→ **Confirmação**
→ **Auditoria**

A operação somente será considerada financeiramente confirmada após a confirmação dos lançamentos correspondentes.

A alteração de estado e os lançamentos financeiros deverão ocorrer dentro da mesma transação de banco de dados quando tecnicamente aplicável.

---

### 28.6 Imutabilidade

Após uma transação financeira ser confirmada:

- seu valor não poderá ser alterado;
- sua conta não poderá ser alterada;
- seu ciclo não poderá ser alterado;
- sua origem não poderá ser substituída;
- seus lançamentos não poderão ser apagados.

Correções deverão ocorrer por meio de nova operação formal.

O mecanismo será:

**Lançamento original**
→ **Reversão**
→ **Novo lançamento correto**

O histórico original deverá permanecer disponível para auditoria.

---

### 28.7 Estornos

Um estorno nunca deverá apagar o pagamento original.

Fluxo:

**Pagamento confirmado**
→ **Estorno**
→ **Lançamento de reversão**

O estorno deverá possuir:

- referência ao lançamento original;
- motivo;
- data/hora;
- operador ou origem automática;
- identificador de idempotência;
- registro de auditoria.

O valor estornado deverá deixar de participar do cálculo de participação financeira na medida em que deixar de estar efetivamente disponível.

Não poderá existir estorno duplicado para a mesma operação.

---

### 28.8 Idempotência

Toda operação financeira crítica deverá possuir proteção contra duplicidade.

Exemplos:

- confirmação de pagamento;
- webhook de pagamento;
- estorno;
- liberação de empréstimo;
- pagamento de parcela;
- distribuição;
- lançamento de multa;
- lançamento de juros.

Uma mesma operação recebida duas ou mais vezes deverá produzir somente um efeito financeiro.

O sistema deverá identificar a operação por mecanismos como:

- `idempotency_key`;
- `external_transaction_id`;
- `webhook_id`;
- identificador interno da operação.

Requisições repetidas poderão retornar o resultado da operação já processada, sem criar novo lançamento.

---

### 28.9 Participação financeira

A participação financeira será baseada somente em capital efetivamente confirmado.

Para cada contribuição:

**capital-days = valor confirmado × dias elegíveis**

A participação individual será:

**participação individual = soma dos capital-days**

A participação no resultado será:

**resultado individual = resultado distribuível × participação individual / participação total elegível**

Valores:

- previstos;
- pendentes;
- rejeitados;
- cancelados;
- estornados;

não deverão participar enquanto não representarem capital efetivamente disponível.

---

### 28.10 Pagamento parcial

Uma contribuição parcialmente paga deverá permanecer identificada como:

`PARTIALLY_PAID`

Somente o valor efetivamente confirmado deverá gerar participação financeira.

Exemplo:

Valor devido: R$150,00

Valor confirmado: R$100,00

Participação financeira:

**R$100,00 × dias elegíveis**

Os R$50,00 restantes permanecerão como saldo devido.

A confirmação posterior dos R$50,00 deverá gerar novo evento financeiro e iniciar sua própria participação conforme a data efetiva de confirmação.

---

### 28.11 Datas financeiras

A data utilizada para participação financeira deverá ser a data de confirmação financeira válida.

Não deverá ser utilizada:

- data de criação da cobrança;
- data de geração do Pix;
- data de envio da cobrança;
- data informada manualmente pelo usuário.

A convenção exata de inclusão/exclusão do primeiro e último dia deverá ser definida no Motor Financeiro e aplicada de maneira uniforme.

---

### 28.12 Separação entre caixa, resultado e recebíveis

O Ledger deverá permitir distinguir:

- dinheiro disponível em caixa;
- contribuições confirmadas;
- juros efetivamente recebidos;
- multas efetivamente recebidas;
- despesas realizadas;
- valores a receber;
- principal de empréstimos em aberto;
- resultado realizado;
- valores distribuídos.

Principal de empréstimo ainda não recebido não deverá ser tratado como receita.

Juros futuros de empréstimo não deverão ser tratados como receita realizada antes de sua constituição e recebimento conforme as regras financeiras aplicáveis.

---

### 28.13 Taxa de administração

No fechamento do ciclo, o resultado realizado deverá considerar:

**juros efetivamente recebidos**
+
**multas efetivamente recebidas**
+
**outros resultados financeiros válidos**
-
**10% de taxa de administração/manutenção**

A taxa deverá ser registrada no Ledger como operação própria e rastreável.

Não deverá ser descontada silenciosamente durante os cálculos individuais.

---

### 28.14 Conciliação

Os valores deverão ser conciliados entre:

**Mercado Pago**
↕
**FRcaixinha**
↕
**Ledger**

A reconciliação deverá verificar, entre outros:

- identificador externo;
- valor;
- data;
- status;
- usuário;
- ciclo;
- operação correspondente.

Divergências deverão possuir status próprio.

Divergências materiais não resolvidas deverão bloquear o fechamento e a distribuição do ciclo.

---

### 28.15 Concorrência

Operações financeiras simultâneas deverão ser protegidas contra condições de corrida.

O backend deverá utilizar mecanismos adequados de:

- transação;
- isolamento;
- locks;
- constraints;
- revalidação antes da confirmação.

Exemplo:

Duas solicitações simultâneas de empréstimo não poderão consumir a mesma liquidez disponível caso uma delas ultrapasse os limites financeiros após a primeira operação ser confirmada.

A decisão final deverá ocorrer no backend dentro de uma operação transacional segura.

---

### 28.16 Integridade do saldo

O sistema deverá impedir:

- saldo negativo não autorizado;
- lançamento sem conta;
- lançamento sem ciclo quando obrigatório;
- transação sem partidas equilibradas;
- duplicidade financeira;
- alteração retroativa de lançamento confirmado;
- exclusão de histórico financeiro;
- distribuição superior ao valor distribuível;
- uso de valor não confirmado como capital;
- migração silenciosa entre ciclos.

Constraints do banco e validações do backend deverão trabalhar conjuntamente.

---

### 28.17 Fechamento

Antes do fechamento de um ciclo, o sistema deverá validar:

1. pagamentos;
2. estornos;
3. conciliação;
4. Ledger;
5. empréstimos;
6. recebíveis;
7. despesas;
8. resultado realizado;
9. taxa de administração;
10. participação individual;
11. valor distribuível;
12. snapshot financeiro;
13. auditoria.

Se uma validação crítica falhar, o fechamento deverá ser interrompido.

---

### 28.18 Snapshot financeiro

Antes da distribuição deverá ser criado um snapshot imutável do ciclo.

O snapshot deverá registrar, no mínimo:

- ciclo;
- data/hora;
- caixa;
- contribuições confirmadas;
- valores estornados;
- recebíveis;
- empréstimos em aberto;
- juros recebidos;
- multas recebidas;
- despesas válidas;
- taxa de administração;
- resultado realizado;
- resultado distribuível;
- total de capital-days;
- quantidade de participantes elegíveis.

Após homologação do fechamento, o snapshot não poderá ser alterado.

Qualquer correção posterior deverá gerar procedimento formal de ajuste e novo registro auditável.

---

### 28.19 Auditoria financeira

Toda operação financeira crítica deverá permitir reconstruir:

**quem**
→ **o quê**
→ **quando**
→ **por qual origem**
→ **em qual ciclo**
→ **qual valor**
→ **qual lançamento**
→ **qual resultado**

A auditoria deverá permitir
permitir:

- identificar o usuário ou administrador responsável;
- identificar a operação realizada;
- registrar data e hora;
- registrar origem da operação;
- registrar o ciclo financeiro;
- registrar o valor anterior;
- registrar o valor novo;
- registrar o lançamento contábil relacionado;
- registrar o resultado produzido;
- registrar o identificador da operação;
- registrar o identificador de idempotência quando aplicável;
- preservar o registro original sem exclusão física;
- registrar correções por meio de novos lançamentos auditáveis;
- permitir reconstrução cronológica da operação.

Operações críticas incluem, no mínimo:

- confirmação de pagamento;
- estorno;
- lançamento no Ledger;
- criação ou alteração de contribuição;
- criação, aprovação, liberação ou quitação de empréstimo;
- aplicação de multa ou penalidade;
- inclusão ou alteração de garantia;
- aprovação de crédito especial;
- fechamento de ciclo;
- apuração de resultado;
- distribuição de resultado;
- alteração de política financeira;
- alteração de permissões administrativas.

A auditoria financeira deverá ser independente do fluxo de apresentação do aplicativo.

O aplicativo poderá consultar os registros de auditoria, mas não poderá permitir que usuário comum ou administrador sem autorização os apague ou altere.

Registros de auditoria financeira deverão possuir retenção compatível com as obrigações legais, contratuais e operacionais aplicáveis.

---

### 28.20 Integridade entre Auditoria e Ledger

Toda operação financeira que produza impacto no saldo, patrimônio, resultado, obrigação ou participação deverá possuir correspondência verificável no Ledger.

A ausência de correspondência deverá ser tratada como inconsistência financeira.

Inconsistências críticas deverão:

- gerar alerta;
- impedir a homologação do fechamento quando afetarem valores financeiros;
- gerar registro de auditoria;
- permitir investigação e reconciliação;
- nunca ser corrigidas mediante exclusão silenciosa do lançamento original.

Correções financeiras deverão utilizar reversão e novo lançamento quando aplicável.

---

### 28.21 Regra de imutabilidade

Após confirmação financeira, nenhum registro histórico crítico poderá ser simplesmente sobrescrito.

Quando houver necessidade de correção:

REGISTRO ORIGINAL
→ REVERSÃO OU AJUSTE FORMAL
→ NOVO LANÇAMENTO
→ AUDITORIA
→ RECONCILIAÇÃO

A correção deverá preservar a rastreabilidade entre o registro original e o novo registro.

---

### 28.22 Critério de encerramento da auditoria

A camada de auditoria somente será considerada aprovada quando possuir:

- modelo de dados definido;
- identificação do autor da operação;
- identificação da operação;
- data e hora;
- ciclo financeiro;
- valores envolvidos;
- referência ao Ledger;
- rastreabilidade de correções;
- proteção contra alteração indevida;
- testes unitários;
- testes de integração;
- testes de segurança;
- teste de reconstrução de operação;
- teste de concorrência quando aplicável;
- homologação.

Nenhuma operação financeira definitiva deverá ser considerada concluída sem rastreabilidade auditável.

