import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../app.dart';
import '../../models/finance_models.dart';
import '../../repositories/app_repository.dart';
import '../payments/pix_payment_screen.dart';

class LoanDetailScreen extends StatefulWidget {
  final int loanId;
  final AppRepository? repository;
  const LoanDetailScreen({super.key, required this.loanId, this.repository});
  @override
  State<LoanDetailScreen> createState() => _LoanDetailScreenState();
}

class _LoanDetailScreenState extends State<LoanDetailScreen> {
  Map<String, dynamic>? data;
  List<FinancialObligation> obligations = [];
  Map<int, FinancialObligation> installmentObligations = {};
  String? error;

  AppRepository get repository => widget.repository ?? context.read<AppState>().repository;
  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    try {
      final r = await repository.loan(widget.loanId);
      final loadedObligations = await repository.financialObligations();
      final installments = r['installments'] as List<dynamic>? ?? const [];
      final links = <int, FinancialObligation>{
        for (final obligation in loadedObligations)
          if (obligation.type == FinancialObligationType.loanInstallment &&
              installments.any((item) => (item as Map)['id'] == obligation.obligationId))
            obligation.obligationId: obligation,
      };
      if (mounted) {
        setState(() {
          data = r;
          obligations = loadedObligations;
          installmentObligations = links;
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    }
  }

  String statusLabel(FinancialObligationStatus status) => switch (status) {
    FinancialObligationStatus.pending => 'Pendente',
    FinancialObligationStatus.partial => 'Parcial',
    FinancialObligationStatus.overdue => 'Em atraso',
    FinancialObligationStatus.paid => 'Quitado',
  };

  Future<void> pay(Map<String, dynamic> i) async {
    final installmentId = i['id'] as int;
    final obligation = installmentObligations[installmentId];
    try {
      final payment = await repository.createLoanInstallmentPix(installmentId);
      if (!mounted) return;
      await Navigator.push(
        context,
        MaterialPageRoute(
          builder: (_) => PixPaymentScreen(
            repository: repository,
            payment: payment,
            receiptAvailable: obligation?.receiptAvailable ?? false,
            onPaymentConfirmed: () {
              load();
            },
          ),
        ),
      );
      if (mounted) await load();
    } catch (e) {
      if (mounted)
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(e.toString())));
    }
  }

  @override
  Widget build(BuildContext context) {
    if (error != null)
      return Scaffold(
        appBar: AppBar(title: const Text('Empréstimo')),
        body: Center(child: Text(error!)),
      );
    if (data == null)
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    final l = data!;
    final items = l['installments'] as List<dynamic>? ?? [];
    return Scaffold(
      appBar: AppBar(title: Text('Empréstimo #${l['id']}')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Status: ${l['status']}'),
                  Text('Principal: R\$ ${l['principal']}'),
                  Text('Taxa mensal: ${l['monthly_rate']}'),
                  Text('Parcelas: ${l['installments']}'),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),
          const Text(
            'Parcelas',
            style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
          ),
          ...items.map((x) {
            final i = x as Map<String, dynamic>;
            final installmentId = i['id'] as int;
            final obligation = installmentObligations[installmentId];
            final status = obligation?.financialStatus ??
                financialObligationStatusFromJson(i['status']);
            final dueDate = obligation?.dueDate?.toIso8601String().substring(0, 10) ??
                i['due_date'];
            final originalAmount = obligation?.amountDue ?? i['amount'];
            final paidAmount = obligation?.amountPaid ?? i['paid_amount'];
            final outstandingAmount = obligation?.outstandingAmount ?? i['remaining'];
            final principal = obligation?.principalOutstanding ?? i['principal'];
            final interest = obligation?.interestOutstanding ?? i['interest'];
            final penalty = obligation?.penaltyOutstanding ?? i['penalty_amount'];
            return Card(
              child: ListTile(
                title: Text('Parcela ${i['number']} • R\$ $originalAmount'),
                subtitle: Text(
                  'Valor original: R\$ $originalAmount\n'
                  'Valor pago: R\$ $paidAmount\n'
                  'Saldo pendente: R\$ $outstandingAmount\n'
                  'Principal: R\$ $principal\n'
                  'Juros: R\$ $interest\n'
                  'Multa: R\$ $penalty\n'
                  'Vencimento: $dueDate\n'
                  'Estado: ${statusLabel(status)}'
                  '${obligation != null && obligation.daysOverdue > 0 ? ' • ${obligation.daysOverdue} dias em atraso' : ''}',
                ),
                isThreeLine: true,
                trailing:
                    status == FinancialObligationStatus.paid
                        ? const Icon(Icons.check_circle)
                        : ElevatedButton(
                          onPressed: () => pay(i),
                          child: const Text('Pagar Pix'),
                        ),
              ),
            );
          }),
        ],
      ),
    );
  }
}
