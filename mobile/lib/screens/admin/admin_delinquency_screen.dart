import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../app.dart';
import '../../models/finance_models.dart';
import '../../repositories/app_repository.dart';
import '../payments/pix_receipt_screen.dart';

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
  FinancialObligationType? _obligationType;
  FinancialObligationStatus? _selectedStatus;
  bool _overdueOnly = false;
  DateTime? _dueFrom;
  DateTime? _dueTo;

  AppRepository get _repository =>
      widget.repository ?? context.read<AppState>().repository;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<List<AdminDelinquencyItem>> _loadItems() => _repository.adminDelinquency(
        obligationType: _obligationType,
        status: _selectedStatus,
        overdueOnly: _overdueOnly ? true : null,
        dueFrom: _dueFrom,
        dueTo: _dueTo,
      );

  Future<void> _load({bool reloadSummary = true}) async {
    setState(() => _error = null);
    try {
      if (reloadSummary) {
        final results = await Future.wait<Object>([
          _repository.adminDelinquencySummary(),
          _loadItems(),
        ]);
        if (!mounted) return;
        setState(() {
          _summary = results[0] as AdminDelinquencySummary;
          _items = results[1] as List<AdminDelinquencyItem>;
        });
        return;
      }
      final items = await _loadItems();
      if (!mounted) return;
      setState(() => _items = items);
    } catch (error) {
      if (mounted) setState(() => _error = error);
    }
  }

  Future<void> _showFilters() async {
    var obligationType = _obligationType;
    var status = _selectedStatus;
    var overdueOnly = _overdueOnly;
    var dueFrom = _dueFrom;
    var dueTo = _dueTo;

    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (sheetContext) => StatefulBuilder(
        builder: (context, setSheetState) {
          Future<void> selectDate(bool isStart) async {
            final selected = await showDatePicker(
              context: sheetContext,
              initialDate: isStart
                  ? dueFrom ?? DateTime.now()
                  : dueTo ?? dueFrom ?? DateTime.now(),
              firstDate: DateTime(2000),
              lastDate: DateTime(2100),
            );
            if (selected != null) {
              setSheetState(() {
                if (isStart) {
                  dueFrom = selected;
                } else {
                  dueTo = selected;
                }
              });
            }
          }

          Future<void> apply() async {
            if (dueFrom != null && dueTo != null && dueFrom!.isAfter(dueTo!)) {
              ScaffoldMessenger.of(this.context).showSnackBar(
                const SnackBar(
                  content: Text('A data inicial não pode ser posterior à data final.'),
                ),
              );
              return;
            }
            Navigator.pop(sheetContext);
            setState(() {
              _obligationType = obligationType;
              _selectedStatus = status;
              _overdueOnly = overdueOnly;
              _dueFrom = dueFrom;
              _dueTo = dueTo;
            });
            await _load(reloadSummary: false);
          }

          Future<void> clear() async {
            Navigator.pop(sheetContext);
            setState(() {
              _obligationType = null;
              _selectedStatus = null;
              _overdueOnly = false;
              _dueFrom = null;
              _dueTo = null;
            });
            await _load(reloadSummary: false);
          }

          return SafeArea(
            child: Padding(
              padding: EdgeInsets.fromLTRB(
                16,
                16,
                16,
                16 + MediaQuery.viewInsetsOf(context).bottom,
              ),
              child: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text('Filtros', style: Theme.of(context).textTheme.titleLarge),
                    const SizedBox(height: 16),
                    DropdownButtonFormField<FinancialObligationType?>(
                      key: const Key('delinquency-filter-type'),
                      initialValue: obligationType,
                      decoration: const InputDecoration(labelText: 'Tipo'),
                      items: const [
                        DropdownMenuItem(
                          key: Key('delinquency-filter-type-all'),
                          value: null,
                          child: Text('Todos'),
                        ),
                        DropdownMenuItem(
                          key: Key('delinquency-filter-type-contribution'),
                          value: FinancialObligationType.contribution,
                          child: Text('Contribuição'),
                        ),
                        DropdownMenuItem(
                          key: Key('delinquency-filter-type-installment'),
                          value: FinancialObligationType.loanInstallment,
                          child: Text('Parcela'),
                        ),
                      ],
                      onChanged: (value) => setSheetState(() => obligationType = value),
                    ),
                    const SizedBox(height: 12),
                    DropdownButtonFormField<FinancialObligationStatus?>(
                      key: const Key('delinquency-filter-status'),
                      initialValue: status,
                      decoration: const InputDecoration(labelText: 'Situação'),
                      items: const [
                        DropdownMenuItem(
                          key: Key('delinquency-filter-status-all'),
                          value: null,
                          child: Text('Todos'),
                        ),
                        DropdownMenuItem(
                          key: Key('delinquency-filter-status-pending'),
                          value: FinancialObligationStatus.pending,
                          child: Text('Pendente'),
                        ),
                        DropdownMenuItem(
                          key: Key('delinquency-filter-status-partial'),
                          value: FinancialObligationStatus.partial,
                          child: Text('Parcial'),
                        ),
                        DropdownMenuItem(
                          key: Key('delinquency-filter-status-overdue'),
                          value: FinancialObligationStatus.overdue,
                          child: Text('Em atraso'),
                        ),
                      ],
                      onChanged: (value) => setSheetState(() => status = value),
                    ),
                    SwitchListTile(
                      key: const Key('delinquency-filter-overdue'),
                      contentPadding: EdgeInsets.zero,
                      title: const Text('Somente vencidos'),
                      value: overdueOnly,
                      onChanged: (value) => setSheetState(() => overdueOnly = value),
                    ),
                    OutlinedButton(
                      key: const Key('delinquency-filter-due-from'),
                      onPressed: () => selectDate(true),
                      child: Text('Data inicial de vencimento: ${_date(dueFrom)}'),
                    ),
                    const SizedBox(height: 8),
                    OutlinedButton(
                      key: const Key('delinquency-filter-due-to'),
                      onPressed: () => selectDate(false),
                      child: Text('Data final de vencimento: ${_date(dueTo)}'),
                    ),
                    const SizedBox(height: 16),
                    Row(
                      children: [
                        Expanded(
                          child: OutlinedButton(
                            key: const Key('delinquency-filter-clear'),
                            onPressed: clear,
                            child: const Text('Limpar filtros'),
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: FilledButton(
                            key: const Key('delinquency-filter-apply'),
                            onPressed: apply,
                            child: const Text('Aplicar'),
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          );
        },
      ),
    );
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
    final paymentId = item.paymentId;
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
            if (item.receiptAvailable && paymentId != null)
              TextButton(
                onPressed: () {
                  final repository = _repository;
                  Navigator.of(context).push(MaterialPageRoute<void>(
                    builder: (_) => PixReceiptScreen(
                      paymentId: paymentId,
                      repository: repository,
                    ),
                  ));
                },
                child: const Text('Ver recibo'),
              ),
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
      appBar: AppBar(
        title: const Text('Inadimplência administrativa'),
        actions: [
          TextButton.icon(
            onPressed: _showFilters,
            icon: const Icon(Icons.filter_list),
            label: const Text('Filtros'),
          ),
        ],
      ),
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
