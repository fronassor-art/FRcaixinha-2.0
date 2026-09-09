# FRcaixinha 1.0 — Catálogo de Regras

Este documento transforma as regras da Especificação Master em identificadores
técnicos verificáveis.

Nenhuma regra financeira deverá ser considerada implementada sem código,
teste e homologação.

---

# 1. IDENTIDADE E ACESSO

## AUTH-001 — Identidade única

Cada usuário deverá possuir uma identidade única no sistema.

O sistema não deverá permitir duplicidade de identidade incompatível com
as regras de cadastro.

## AUTH-002 — Acesso ao grupo fechado

O acesso ao grupo deverá ocorrer somente mediante:

INVITE
→ REGISTRATION
→ CONTRACT_ACCEPTANCE
→ PAYMENT
→ PAYMENT_VALIDATION
→ ADMIN_REVIEW
→ ACTIVE_MEMBERSHIP

Nenhum cliente poderá tornar-se membro ativo somente através do aplicativo
sem validação das etapas obrigatórias.

---

# 2. MEMBROS E CICLOS

## MEM-001 — Uma quota por participante

Cada participante poderá possuir no máximo uma quota ativa no mesmo ciclo.

## MEM-002 — Limite de quotas

O ciclo deverá respeitar o limite máximo de quotas configurado.

Ciclo 001:
50 quotas.

O limite pertence ao ciclo e não deverá ser tratado como constante global.

## MEM-003 — Período de entrada

No ciclo 001, novas entradas somente serão permitidas até 10/01/2027,
observadas as demais condições de admissão.

Após o encerramento do período de entrada, novas admissões deverão ser
bloqueadas pelo backend.

## MEM-004 — Entrada com pagamento inicial

A nova entrada do ciclo 001 deverá exigir o pagamento das contribuições
iniciais previstas antes da ativação definitiva da participação.

---

# 3. CONTRIBUIÇÕES

## FIN-001 — Valor da contribuição

O valor da contribuição pertence ao ciclo.

Ciclo 001:
R$150,00 por contribuição mensal.

Alterações de valor em ciclos futuros não poderão alterar retroativamente
as contribuições de ciclos já homologados.

## FIN-002 — Vencimento

A contribuição possui vencimento no dia 10.

Quando a data calculada não for dia útil financeiro, deverá ser utilizada
a próxima data útil definida pelo Financial Calendar.

A data original e a data ajustada deverão ser preservadas.

## FIN-003 — Recesso

O mês de recesso não elimina nem suspende a contribuição prevista para
o ciclo.

## FIN-004 — Pagamento parcial

Uma contribuição poderá possuir pagamento parcial.

O registro deverá permanecer com estado PARTIALLY_PAID enquanto houver
saldo financeiro pendente.

Somente o valor efetivamente confirmado poderá participar do cálculo
financeiro.

## FIN-005 — Pagamento confirmado

Somente valores financeiramente confirmados poderão:

- alterar saldo financeiro;
- gerar participação;
- entrar no Ledger definitivo;
- participar do cálculo de capital-days.

---

# 4. INADIMPLÊNCIA

## FIN-REF-001 — Multa da contribuição

Até 29 dias corridos de atraso:

multa fixa = R$10,00.

## FIN-REF-002 — Encargo após 30 dias

A partir de 30 dias completos de atraso:

multa fixa = R$10,00

mais

10% sobre o principal vencido para cada período completo de 30 dias.

O percentual deverá incidir somente sobre o principal vencido.

Não deverá incidir sobre multas ou encargos anteriores.

## FIN-REF-003 — Não capitalização de penalidades

Multas e encargos não deverão gerar novos encargos sobre si mesmos.

## FIN-REF-004 — Inadimplência de contribuição

A contribuição vencida deverá permanecer identificada como obrigação
financeira até sua regularização, baixa, ajuste formal ou tratamento
previsto no fechamento.

---

# 5. NOTIFICAÇÕES

## NOTIF-001 — Aviso de inadimplência

Participante inadimplente deverá receber comunicação periódica a cada
2 dias.

Canais:

- e-mail;
- push;
- WhatsApp.

## NOTIF-002 — Conteúdo mínimo

A comunicação deverá informar:

- contribuição vencida;
- dias de atraso;
- valor atualizado;
- informações para regularização;
- Pix quando disponível.

---

# 6. LEDGER

## LEDGER-001 — Registro financeiro

Toda operação financeira confirmada deverá possuir registro no Ledger.

## LEDGER-002 — Imutabilidade

Registro financeiro histórico confirmado não poderá ser sobrescrito ou
apagado para alterar seu significado econômico.

## LEDGER-003 — Correção

Correções financeiras deverão utilizar:

REGISTRO ORIGINAL
→ REVERSÃO OU AJUSTE FORMAL
→ NOVO LANÇAMENTO
→ AUDITORIA
→ RECONCILIAÇÃO

## LEDGER-004 — Rastreabilidade

Todo lançamento deverá permitir identificar:

- quem;
- o quê;
- quando;
- origem;
- ciclo;
- valor;
- lançamento relacionado;
- resultado financeiro.

---

# 7. ESTORNO

## PAY-001 — Estorno

Estorno não deverá apagar o pagamento original.

Deverá ser criado novo lançamento de reversão relacionado ao registro
original.

## PAY-002 — Participação após estorno

O valor efetivamente revertido deverá ser retirado do cálculo de
participação financeira correspondente.

O histórico original deverá permanecer preservado.

---

# 8. CAPITAL-DAYS

## DIST-001 — Participação por capital-days

A participação individual será calculada através de:

valor confirmado × dias elegíveis.

A participação total do participante será a soma de seus capital-days.

## DIST-002 — Distribuição proporcional

O resultado distribuível individual será:

resultado distribuível
×
capital-days individuais
/
capital-days totais elegíveis.

## DIST-003 — Somente dinheiro confirmado

Valores não confirmados não poderão gerar capital-days.

## DIST-004 — Pagamento parcial

Em pagamento parcial, somente o valor confirmado participará do cálculo.

## DIST-005 — Estorno

Valor revertido deverá deixar de participar do cálculo de capital-days,
preservando-se o histórico da operação.

---

# 9. RESULTADO FINANCEIRO

## FIN-010 — Resultado realizado

O resultado financeiro deverá considerar somente valores efetivamente
realizados.

Recebíveis futuros não deverão ser tratados como caixa ou lucro realizado.

## FIN-011 — Taxa administrativa

Na apuração do ciclo deverá ser considerada a taxa de administração/
manutenção de 10% sobre o resultado de juros e demais resultados
financeiros válidos definidos pela especificação.

## FIN-012 — Separação financeira

O sistema deverá manter separação entre:

- caixa;
- resultado realizado;
- recebíveis;
- obrigações;
- valores distribuíveis.

---

# 10. EMPRÉSTIMOS

## LOAN-001 — Juros sobre saldo devedor

Empréstimos utilizarão juros de 20% ao mês sobre o saldo devedor.

## LOAN-002 — Amortização

A amortização deverá reduzir o saldo devedor.

juros = saldo anterior × 20%

amortização = parcela - juros

saldo novo = saldo anterior - amortização

## LOAN-003 — Parcelamento

O prazo normal deverá ser de 1 a 6 parcelas.

## LOAN-004 — Ajuste da última parcela

A última parcela poderá receber ajuste de centavos para garantir:

saldo final = R$0,00.

## LOAN-005 — Quitação antecipada

Na quitação antecipada não deverão ser cobrados juros futuros ainda
não constituídos.

O valor da quitação deverá considerar:

principal atual
+
encargos já constituídos
+
obrigações vencidas aplicáveis.

---

# 11. INADIMPLÊNCIA DO EMPRÉSTIMO

## LOAN-010 — Multa da parcela

Parcela vencida deverá receber multa fixa de R$10,00 conforme a regra
aplicável ao empréstimo.

## LOAN-011 — Encargo após 30 dias

Após 30 dias completos de atraso deverá ser aplicado adicional de 10%
sobre o principal da parcela vencida.

O adicional não deverá incidir sobre multas ou encargos anteriores.

---

# 12. GARANTIAS

## GAR-001 — Garantia de joia

Quando aplicável, a joia deverá ser avaliada na Caixa Econômica Federal
para fins de referência de valor.

## GAR-002 — Valor de garantia

Para fins de garantia do sistema:

valor de garantia = 80% do valor avaliado.

## GAR-003 — Registro digital

A garantia deverá possuir registro contendo, quando aplicável:

- identificador;
- participante;
- empréstimo;
- descrição;
- fotografias;
- documento de avaliação;
- valor avaliado;
- valor de garantia;
- data;
- custódia;
- status.

---

# 13. FECHAMENTO

## CLOSE-001 — Fechamento por ciclo

Cada ciclo deverá possuir fechamento financeiro próprio.

## CLOSE-002 — Snapshot

Após homologação, o snapshot financeiro deverá ser imutável.

## CLOSE-003 — Correção pós-fechamento

Correção posterior deverá utilizar procedimento formal e novo registro
auditável.

## CLOSE-004 — Empréstimo aberto

Empréstimo aberto permanecerá vinculado ao ciclo de origem.

Seu saldo devedor será tratado como recebível daquele ciclo.

Não deverá ser transferido silenciosamente para o ciclo seguinte.

## CLOSE-005 — Recebível não é caixa

Principal de empréstimo ainda não recebido não poderá ser tratado como
dinheiro disponível nem como resultado realizado.

---

# 14. AUDITORIA

## AUDIT-001 — Operação crítica rastreável

Operação financeira crítica deverá permitir reconstruir:

- quem;
- o quê;
- quando;
- origem;
- ciclo;
- valor;
- lançamento;
- resultado.

## AUDIT-002 — Correção rastreável

Toda correção deverá manter vínculo entre registro original e novo registro.

## AUDIT-003 — Proteção contra alteração

Registro histórico crítico deverá possuir proteção contra alteração
indevida.

---

# 15. IDEMPOTÊNCIA

## IDEMP-001 — Operação repetida

Operações financeiras deverão possuir mecanismo de idempotência.

Uma mesma operação externa confirmada não poderá gerar duplicidade
financeira.

## IDEMP-002 — Identificador externo

Eventos financeiros externos deverão possuir identificador único
quando fornecido pelo provedor.

---

# 16. REGRA DE IMPLEMENTAÇÃO

## TEST-001 — Definition of Done

Uma regra somente será considerada implementada quando possuir:

- especificação;
- implementação;
- teste unitário;
- teste de integração;
- teste financeiro/segurança quando aplicável;
- homologação.

Nenhuma regra financeira deverá ser considerada concluída somente porque
o código compila.

