import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../app.dart';
import '../models/finance_models.dart';
import '../repositories/app_repository.dart';

class FinancialObligationsScreen extends StatefulWidget {
  final AppRepository? repository;
  const FinancialObligationsScreen({super.key, this.repository});
  @override State<FinancialObligationsScreen> createState() => _FinancialObligationsScreenState();
}

class _FinancialObligationsScreenState extends State<FinancialObligationsScreen> {
  List<FinancialObligation>? _items;
  Object? _error;
  AppRepository get _repository => widget.repository ?? context.read<AppState>().repository;
  @override void initState() { super.initState(); _load(); }
  Future<void> _load() async {
    setState(() => _error = null);
    try { final items = await _repository.financialObligations(); if (mounted) setState(() => _items = items); }
    catch (error) { if (mounted) setState(() => _error = error); }
  }
  String _status(FinancialObligationStatus status) => switch (status) { FinancialObligationStatus.pending => 'Pendente', FinancialObligationStatus.partial => 'Parcial', FinancialObligationStatus.overdue => 'Em atraso', FinancialObligationStatus.paid => 'Quitado' };
  String _date(DateTime? date) => date == null ? 'Não informado' : '${date.day.toString().padLeft(2, '0')}/${date.month.toString().padLeft(2, '0')}/${date.year}';
  bool _hasAmount(String amount) => amount != '0' && amount != '0.0' && amount != '0.00';
  Widget _item(FinancialObligation item) {
    final contribution = item.type == FinancialObligationType.contribution;
    final lines = <String>[if (contribution && item.competence != null) 'Competência: ${item.competence}', if (!contribution && item.installmentNumber != null) 'Parcela: ${item.installmentNumber}${item.loanId == null ? '' : ' • Empréstimo #${item.loanId}'}', 'Vencimento: ${_date(item.dueDate)}', 'Valor original: R\$ ${item.amountDue}', 'Valor pago: R\$ ${item.amountPaid}', 'Saldo: R\$ ${item.outstandingAmount}', if (!contribution && _hasAmount(item.principalOutstanding)) 'Principal pendente: R\$ ${item.principalOutstanding}', if (!contribution && _hasAmount(item.interestOutstanding)) 'Juros pendentes: R\$ ${item.interestOutstanding}', if (!contribution && _hasAmount(item.penaltyOutstanding)) 'Multa pendente: R\$ ${item.penaltyOutstanding}', 'Estado: ${_status(item.financialStatus)}', if (item.financialStatus == FinancialObligationStatus.overdue && item.daysOverdue > 0) '${item.daysOverdue} dia${item.daysOverdue == 1 ? '' : 's'} em atraso', if (item.receiptAvailable) 'Recibo disponível'];
    return Card(child: Padding(padding: const EdgeInsets.all(16), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(contribution ? 'Contribuição' : 'Parcela do empréstimo', style: Theme.of(context).textTheme.titleMedium), const SizedBox(height: 8), ...lines.map((line) => Padding(padding: const EdgeInsets.only(bottom: 3), child: Text(line)))])));
  }
  @override Widget build(BuildContext context) {
    final items = _items;
    return Scaffold(appBar: AppBar(title: const Text('Minhas obrigações')), body: _error != null ? Center(child: Padding(padding: const EdgeInsets.all(24), child: Column(mainAxisSize: MainAxisSize.min, children: [Text('Não foi possível carregar as obrigações.\n$_error', textAlign: TextAlign.center), const SizedBox(height: 12), FilledButton(onPressed: _load, child: const Text('Tentar novamente'))]))) : items == null ? const Center(child: CircularProgressIndicator()) : RefreshIndicator(onRefresh: _load, child: items.isEmpty ? ListView(physics: const AlwaysScrollableScrollPhysics(), children: const [SizedBox(height: 180), Center(child: Text('Nenhuma obrigação financeira encontrada.'))]) : ListView(physics: const AlwaysScrollableScrollPhysics(), padding: const EdgeInsets.all(16), children: items.map(_item).toList())));
  }
}
