import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import '../../app.dart';
import '../../models/finance_models.dart';
import '../../repositories/app_repository.dart';

class LoanRequestScreen extends StatefulWidget {
  final AppRepository? repository;

  const LoanRequestScreen({super.key, this.repository});

  @override
  State<LoanRequestScreen> createState() => _LoanRequestScreenState();
}

class _LoanRequestScreenState extends State<LoanRequestScreen> {
  final principal = TextEditingController();
  int installments = 1;
  LoanSimulation? simulation;
  bool confirmed = false;
  bool simulating = false;
  bool confirming = false;
  bool submitting = false;
  String? error;

  AppRepository get repository =>
      widget.repository ?? context.read<AppState>().repository;

  bool get busy => simulating || confirming || submitting;
  bool get canSubmit => simulation != null && confirmed && !busy;

  String? get principalValue {
    final value = principal.text.trim().replaceAll(',', '.');
    final parsed = double.tryParse(value);
    return parsed != null && parsed > 0 ? value : null;
  }

  void invalidateSimulation() {
    if (!mounted || (simulation == null && !confirmed)) return;
    setState(() {
      simulation = null;
      confirmed = false;
      error = null;
    });
  }

  Future<void> simulate() async {
    final value = principalValue;
    if (value == null) {
      setState(() => error = 'Informe um valor principal maior que zero.');
      return;
    }
    setState(() {
      simulating = true;
      error = null;
      simulation = null;
      confirmed = false;
    });
    try {
      final result = await repository.simulateLoan(
        principal: value,
        installments: installments,
      );
      if (mounted) setState(() => simulation = result);
    } catch (exception) {
      if (mounted) setState(() => error = exception.toString());
    } finally {
      if (mounted) setState(() => simulating = false);
    }
  }

  Future<void> confirmSimulation() async {
    final current = simulation;
    if (current == null) return;
    setState(() {
      confirming = true;
      error = null;
    });
    try {
      await repository.confirmLoanSimulation(current.simulationToken);
      if (mounted) setState(() => confirmed = true);
    } catch (exception) {
      if (mounted) setState(() => error = exception.toString());
    } finally {
      if (mounted) setState(() => confirming = false);
    }
  }

  Future<void> submit() async {
    final current = simulation;
    final value = principalValue;
    if (current == null || !confirmed || value == null) return;
    setState(() {
      submitting = true;
      error = null;
    });
    try {
      await repository.requestLoan(
        principal: value,
        installments: installments,
        simulationToken: current.simulationToken,
      );
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Solicitação enviada para análise.')),
      );
      context.pop();
    } catch (exception) {
      if (mounted) setState(() => error = exception.toString());
    } finally {
      if (mounted) setState(() => submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Solicitar empréstimo')),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          TextField(
            controller: principal,
            onChanged: (_) => invalidateSimulation(),
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: const InputDecoration(
              labelText: 'Valor principal',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
          const Text('Taxa mensal fixa: 20%'),
          const SizedBox(height: 12),
          DropdownButtonFormField<int>(
            key: ValueKey(installments),
            initialValue: installments,
            decoration: const InputDecoration(
              labelText: 'Quantidade de parcelas',
              border: OutlineInputBorder(),
            ),
            items: List.generate(
              6,
              (index) => DropdownMenuItem(
                value: index + 1,
                child: Text((index + 1).toString()),
              ),
            ),
            onChanged: busy
                ? null
                : (value) {
                    if (value == null || value == installments) return;
                    setState(() {
                      installments = value;
                      simulation = null;
                      confirmed = false;
                      error = null;
                    });
                  },
          ),
          const SizedBox(height: 16),
          OutlinedButton(
            onPressed: busy ? null : simulate,
            child: Text(simulating ? 'Simulando...' : 'Simular condições'),
          ),
          if (simulation != null) ...[
            const SizedBox(height: 16),
            SimulationCard(simulation: simulation!),
            const SizedBox(height: 12),
            if (confirmed)
              const ListTile(
                leading: Icon(Icons.verified, color: Colors.green),
                title: Text('Simulação confirmada'),
              )
            else
              FilledButton.tonal(
                onPressed: confirming ? null : confirmSimulation,
                child: Text(
                  confirming ? 'Confirmando...' : 'Confirmar simulação',
                ),
              ),
          ],
          if (error != null)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Text(error!),
            ),
          const SizedBox(height: 20),
          FilledButton(
            onPressed: canSubmit ? submit : null,
            child: Text(
              submitting ? 'Enviando...' : 'Solicitar empréstimo',
            ),
          ),
        ],
      ),
    );
  }

  @override
  void dispose() {
    principal.dispose();
    super.dispose();
  }
}

class SimulationCard extends StatelessWidget {
  final LoanSimulation simulation;

  const SimulationCard({super.key, required this.simulation});

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Simulação',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
            ),
            Text('Principal: R\$ ' + simulation.principal),
            Text('Taxa mensal: ' + simulation.monthlyRate),
            Text('Parcelas: ' + simulation.installments.toString()),
            const Divider(),
            ...simulation.schedule.map(
              (item) => ListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(
                  'Parcela ' + item.number.toString() + ' • R\$ ' + item.amount,
                ),
                subtitle: Text(
                  'Amortização: R\$ ' + item.principal +
                      ' • Juros: R\$ ' + item.interest +
                      '\nSaldo: R\$ ' + item.balanceBefore +
                      ' → R\$ ' + item.balanceAfter,
                ),
                isThreeLine: true,
              ),
            ),
            const Divider(),
            Text('Total principal: R\$ ' + simulation.totals.principal),
            Text('Saldo final: R\$ ' + simulation.totals.finalBalance),
            Text('Total juros: R\$ ' + simulation.totals.interest),
            Text('Total pagamentos: R\$ ' + simulation.totals.payment),
          ],
        ),
      ),
    );
  }
}
