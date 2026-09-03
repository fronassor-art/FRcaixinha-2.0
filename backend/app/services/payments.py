class MercadoPagoAdapter:
    """Adaptador de pagamento. Não contém credenciais nem chamadas reais.

    A implementação de produção deve validar a assinatura/evento recebido,
    consultar o pagamento no provedor e aplicar idempotência antes de liquidar.
    """
    provider = "mercado_pago"

    def create_pix_payment(self, *args, **kwargs):
        raise NotImplementedError("Configuração do Mercado Pago ainda não habilitada.")

    def process_webhook(self, *args, **kwargs):
        raise NotImplementedError("Webhook de produção será implementado na próxima etapa.")
