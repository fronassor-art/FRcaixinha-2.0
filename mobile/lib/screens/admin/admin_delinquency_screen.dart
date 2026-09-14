import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../app.dart';
import '../../models/finance_models.dart';
import '../../repositories/app_repository.dart';

class AdminDelinquencyScreen extends StatefulWidget {
  final AppRepository? repository;

  const AdminDelinquencyScreen({super.key, this.repository});

  @override
  State<AdminDelinquencyScreen> createState() => _AdminDelinquencyScreenState();
}

class _AdminDelinquencyScreenState extends State<AdminDelinquencyScreen> {
  AdminDelinquencySummary? _summary;
  List<AdminDelinquencyItem>? _items;
  Object? _error;

  AppRepository get _repository =>
      widget.repository ?? context.read<AppState>().repository;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final results = await Future.wait<Object>([
        _repository.adminDelinquencySummary(),
        _repository.adminDelinquency(),
      ]);
      if (!mounted) return;
      setState(() {
        _summary = results[0] as AdminDelinquencySummary;
        _items = results[1] as List<AdminDelinquencyItem>;
      });
    } catch (error) {
      if (mounted) setState(() => _error = error);
    }
  }

  String _money(String value) => 'R\$ $value';

  String _date(DateTime? date) => date == null
      ? 'Não informado'
      : '${date.day.toString().padLeft(2, '0')}/${date.month.toString().padLeft(2, '0')}/${date.year}';

  String _status(FinancialObligationStatus status) => switch (status) {
        FinancialObligationStatus.pending => 'Pendente',
        FinancialObligationStatus.partial => 'Parcial',
        FinancialObligationStatus.overdue => 'Em atraso',
        FinancialObligationStatus.paid => 'Quitado',
      };

  String _type(AdminDelinquencyItem item) =>
      item.obligationType == FinancialObligationType.contribution
          ? 'Contribuição'
          : 'Parcela do empréstimo';

  bool _hasAmount(String amount) =>
      amount != '0' && amount != '0.0' && amount != '0.00';

  Widget _metric(String label, String value) => SizedBox(
        width: 155,
        child: Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(label, style: Theme.of(context).textTheme.bodySmall),
              Text(value, style: Theme.of(context).textTheme.titleMedium),
            ],
          ),
        ),
      );

  Widget _summaryCard(AdminDelinquencySummary summary) => Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Wrap(
            spacing: 16,
            children: [
              _metric('Participantes inadimplentes', '${summary.delinquentMembersCount}'),
              _metric('Total em aberto', _money(summary.totalOutstanding)),
              _metric('Total vencido', _money(summary.totalOverdue)),
              _metric('Juros pendentes', _money(summary.totalInterestOutstanding)),
              _metric('Multas pendentes', _money(summary.totalPenaltyOutstanding)),
              _metric('Pendentes', '${summary.pendingCount}'),
              _metric('Parciais', '${summary.partialCount}'),
              _metric('Em atraso', '${summary.overdueCount}'),
            ],
          ),
        ),
      );

  Widget _itemCard(AdminDelinquencyItem item) {
    final isContribution =
        item.obligationType == FinancialObligationType.contribution;
    final identification = isContribution
        ? 'Competência: ${item.competence ?? 'Não informada'}'
        : 'Empréstimo #${item.loanId ?? '-'} • Parcela ${item.installmentNumber ?? '-'}';
    final lines = <String>[
      identification,
      'Vencimento: ${_date(item.dueDate)}',
      'Valor devido: ${_money(item.amountDue)}',
      'Valor pago: ${_money(item.amountPaid)}',
      'Saldo pendente: ${_money(item.outstandingAmount)}',
      'Estado: ${_status(item.status)}',
      if (item.daysOverdue > 0)
        '${item.daysOverdue} dia${item.daysOverdue == 1 ? '' : 's'} em atraso',
      if (!isContribution && _hasAmount(item.principalOutstanding))
        'Principal pendente: ${_money(item.principalOutstanding)}',
      if (_hasAmount(item.interestOutstanding))
        'Juros pendentes: ${_money(item.interestOutstanding)}',
      if (_hasAmount(item.penaltyOutstanding))
        'Multa pendente: ${_money(item.penaltyOutstanding)}',
      if (item.receiptAvailable) 'Recibo disponível',
    ];
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(item.memberName ?? 'Participante #${item.memberId}',
                style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 4),
            Text(_type(item), style: Theme.of(context).textTheme.bodyMedium),
            const SizedBox(height: 8),
            ...lines.map((line) => Padding(
                  padding: const EdgeInsets.only(bottom: 3),
                  child: Text(line),
                )),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final items = _items;
    final summary = _summary;
    if (_error != null) {
      return Scaffold(
        appBar: AppBar(title: const Text('Inadimplência administrativa')),
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text('Não foi possível carregar a inadimplência.\n$_error',
                    textAlign: TextAlign.center),
                const SizedBox(height: 12),
                FilledButton(
                  onPressed: _load,
                  child: const Text('Tentar novamente'),
                ),
              ],
            ),
          ),
        ),
      );
    }
    return Scaffold(
      appBar: AppBar(title: const Text('Inadimplência administrativa')),
      body: summary == null || items == null
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.all(16),
                children: [
                  _summaryCard(summary),
                  const SizedBox(height: 8),
                  if (items.isEmpty)
                    const Padding(
                      padding: EdgeInsets.only(top: 160),
                      child: Center(child: Text('Nenhuma obrigação em aberto.')),
                    )
                  else
                    ...items.map(_itemCard),
                ],
              ),
            ),
    );
  }
}
