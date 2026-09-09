# App Android v0.4

O aplicativo Flutter é a primeira camada móvel do FRcaixinha.

Fluxo implementado:
Login -> token JWT -> perfil do membro -> painel.

Próxima evolução:
- cadastro/ativação de membro
- tela de contribuição
- gerar Pix no backend
- exibir QR Code/copia e cola
- atualizar status de pagamento
- empréstimos e parcelas
- extrato
- notificações
- painel administrativo separado

O app nunca deve receber ou armazenar o Access Token do Mercado Pago. O pagamento é criado no backend.
