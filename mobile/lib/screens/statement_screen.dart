import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app.dart';
import '../models/finance_models.dart';
import '../repositories/app_repository.dart';

class StatementScreen extends StatefulWidget {
  final AppRepository? repository;
  const StatementScreen({super.key, this.repository});

  @override
  State<StatementScreen> createState() => _StatementScreenState();
}

class _StatementScreenState extends State<StatementScreen> {
  MemberStatement? _statement;
  Object? _error;
  bool _loading = true;

  AppRepository get _repository => widget.repository ?? context.read<AppState>().repository;

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
      final statement = await _repository.statement();
      if (!mounted) return;
      setState(() => _statement = statement);
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = error);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  String _dateTime(DateTime? value) {
    if (value == null) return 'Data não informada';
    return value.toLocal().toIso8601String().replaceFirst('T', ' ').substring(0, 16);
  }

  String _typeLabel(String type) {
    switch (type) {
      case 'CONTRIBUTION_PAYMENT': return 'Contribuição';
      case 'LOAN_INSTALLMENT_PAYMENT': return 'Pagamento de parcela';
      case 'LOAN_DISBURSEMENT': return 'Liberação de empréstimo';
      case 'AGREEMENT_INSTALLMENT_PAYMENT': return 'Pagamento de acordo';
      case 'REVERSAL': return 'Reversão';
      default: return type.isEmpty ? 'Movimentação financeira' : type;
    }
  }

  String _directionLabel(StatementMovementDirection direction) {
    switch (direction) {
      case StatementMovementDirection.credit: return 'Entrada';
      case StatementMovementDirection.debit: return 'Saída';
      case StatementMovementDirection.unknown: return 'Movimentação';
    }
  }

  Widget _movement(MemberStatementMovement movement) {
    final isCredit = movement.direction == StatementMovementDirection.credit;
    return Card(
      child: ListTile(
        leading: Icon(isCredit ? Icons.arrow_downward : Icons.arrow_upward, color: isCredit ? Colors.green : Colors.red),
        title: Text('R\$ ${movement.total}'),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(_typeLabel(movement.type), style: const TextStyle(fontWeight: FontWeight.bold)),
            Text(_directionLabel(movement.direction)),
            Text(_dateTime(movement.occurredAt)),
            if (movement.description.isNotEmpty) Text(movement.description),
            if (movement.competence != null) Text('Competência: ${movement.competence}'),
            if (movement.loanId != null) Text('Empréstimo #${movement.loanId}${movement.installmentNumber == null ? '' : ' • Parcela ${movement.installmentNumber}'}'),
            if (movement.principal != null) Text('Principal: R\$ ${movement.principal}'),
            if (movement.interest != null) Text('Juros: R\$ ${movement.interest}'),
            if (movement.penalty != null) Text('Multa: R\$ ${movement.penalty}'),
            if (movement.receiptAvailable) const Text('Recibo disponível'),
          ],
        ),
      ),
    );
  }

  Widget _content(MemberStatement statement) {
    final movements = statement.movements;
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.all(16),
        children: [
          Card(child: Padding(padding: const EdgeInsets.all(16), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Text('Resumo', style: TextStyle(fontWeight: FontWeight.bold)),
            Text('Contribuições pagas: R\$ ${statement.totals.contributionsPaid}'),
            Text('Pagamentos de empréstimos: R\$ ${statement.totals.loanPayments}'),
            Text('Empréstimos em aberto: R\$ ${statement.totals.loanOutstanding}'),
          ]))),
          const SizedBox(height: 12),
          const Text('Movimentações', style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
          if (movements.isEmpty)
            const Padding(padding: EdgeInsets.only(top: 24), child: Center(child: Text('Nenhuma movimentação financeira encontrada.')))
          else
            ...movements.map(_movement),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Meu extrato')),
    body: _loading && _statement == null
      ? const Center(child: CircularProgressIndicator())
      : _error != null
        ? Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
            Text('Não foi possível carregar o extrato: $_error'),
            const SizedBox(height: 12),
            ElevatedButton(onPressed: _load, child: const Text('Tentar novamente')),
          ]))
        : _content(_statement!),
  );
}
