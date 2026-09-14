import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../app.dart';
import '../../models/finance_models.dart';
import '../../repositories/app_repository.dart';

class PixReceiptScreen extends StatefulWidget {
  final int paymentId;
  final AppRepository? repository;

  const PixReceiptScreen({super.key, required this.paymentId, this.repository});

  @override
  State<PixReceiptScreen> createState() => _PixReceiptScreenState();
}

class _PixReceiptScreenState extends State<PixReceiptScreen> {
  PixReceipt? _receipt;
  Object? _error;
  bool _loading = true;

  AppRepository get _repository =>
      widget.repository ?? context.read<AppState>().repository;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final receipt = await _repository.paymentReceipt(widget.paymentId);
      if (!mounted) return;
      setState(() => _receipt = receipt);
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = error);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Widget _line(String label, Object? value) {
    final text = value?.toString();
    if (text == null || text.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: Text('$label: $text'),
    );
  }

  Widget _section(String title, List<Widget> children) => Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(title, style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: 8),
              ...children,
            ],
          ),
        ),
      );

  Widget _ledgerEntry(Map<String, dynamic> entry) => Card(
        margin: const EdgeInsets.only(top: 8),
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _line('Conta', entry['account']),
              _line('Direção', entry['direction']),
              if (entry['amount'] != null) Text('Valor: R\$ ${entry['amount']}'),
              _line('Referência', entry['reference_type']),
              _line('ID da referência', entry['reference_id']),
              _line('Hash do lançamento', entry['entry_hash']),
            ],
          ),
        ),
      );

  Widget _content(PixReceipt receipt) {
    final payment = receipt.payment;
    final obligation = receipt.obligation;
    final amounts = receipt.amounts;
    final confirmation = receipt.confirmation;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        _section('Recibo PIX', [
          _line('Número', receipt.receiptNumber),
          _line('Versão', receipt.receiptVersion),
        ]),
        _section('Pagamento', [
          _line('ID do pagamento', payment.id),
          _line('Provedor', payment.provider),
          _line('ID do pagamento no provedor', payment.providerPaymentId),
          _line('ID da ordem no provedor', payment.providerOrderId),
          _line('Referência externa', payment.externalReference),
          _line('TXID PIX', payment.pixTxid),
          _line('End-to-end ID', payment.endToEndId),
          _line('Tipo de referência', payment.referenceType),
          _line('ID da referência', payment.referenceId),
        ]),
        _section('Obrigação', [
          _line('Tipo', obligation.type),
          _line('Contribuição', obligation.contributionId),
          _line('Parcela de empréstimo', obligation.loanInstallmentId),
          _line('Membro', obligation.memberId),
          _line('Estado antes', obligation.statusBefore),
          _line('Estado após', obligation.statusAfter),
        ]),
        _section('Valores', [
          Text('Recebido: R\$ ${amounts.received}'),
          Text('Aplicado: R\$ ${amounts.applied}'),
          Text('Principal: R\$ ${amounts.principal}'),
          Text('Juros: R\$ ${amounts.interest}'),
          Text('Multa: R\$ ${amounts.penalty}'),
          Text('Excesso: R\$ ${amounts.excess}'),
        ]),
        _section('Confirmação', [
          _line('Origem', confirmation.source),
          _line('Confirmado em', confirmation.confirmedAt),
          _line('Evento de webhook', confirmation.webhookEventId),
        ]),
        if (receipt.ledgerEntries.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text('Lançamentos de ledger', style: Theme.of(context).textTheme.titleMedium),
          ...receipt.ledgerEntries.map(_ledgerEntry),
        ],
      ],
    );
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Recibo PIX')),
        body: _loading && _receipt == null
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? Center(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text('Não foi possível carregar o recibo: $_error'),
                        const SizedBox(height: 12),
                        FilledButton(
                          onPressed: _load,
                          child: const Text('Tentar novamente'),
                        ),
                      ],
                    ),
                  )
                : _content(_receipt!),
      );
}
