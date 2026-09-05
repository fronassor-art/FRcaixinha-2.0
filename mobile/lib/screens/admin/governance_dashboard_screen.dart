import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';

class GovernanceDashboardScreen extends StatefulWidget {
  const GovernanceDashboardScreen({super.key});
  @override
  State<GovernanceDashboardScreen> createState() =>
      _GovernanceDashboardScreenState();
}

class _GovernanceDashboardScreenState extends State<GovernanceDashboardScreen> {
  final api = ApiClient();
  Map<String, dynamic>? d;
  String? error;
  Future<void> load() async {
    api.token = await Session.getToken();
    try {
      d = await api.get('/admin/governance/executive');
      error = null;
    } catch (e) {
      error = e.toString();
    }
    if (mounted) setState(() {});
  }

  @override
  void initState() {
    super.initState();
    load();
  }

  @override
  Widget build(BuildContext context) {
    final x = d;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Governança executiva'),
        actions: [IconButton(onPressed: load, icon: const Icon(Icons.refresh))],
      ),
      body: RefreshIndicator(
        onRefresh: load,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (error != null) Text(error!),
            if (x != null) ...[
              Card(
                child: ListTile(
                  leading: Icon(
                    x['status'] == 'PASS'
                        ? Icons.verified
                        : Icons.warning_amber_outlined,
                  ),
                  title: Text('Status: ${x['status']}'),
                  subtitle: Text(
                    (x['risk_flags'] as List).isEmpty
                        ? 'Sem alertas críticos'
                        : 'Alertas: ${(x['risk_flags'] as List).join(', ')}',
                  ),
                ),
              ),
              _card(
                'Integridade do Ledger',
                x['checks']['ledger_integrity'] ? 'PASS' : 'ATENÇÃO',
              ),
              _card(
                'Última reconciliação',
                x['checks']['reconciliation_latest'] ? 'PASS' : 'ATENÇÃO',
              ),
              _card('Webhooks pendentes', '${x['webhooks']['pending']}'),
              _card(
                'Inadimplência',
                '${x['collections']['overdue_installments']} parcelas • R\$ ${x['collections']['overdue_balance']}',
              ),
              _card('Acordos pendentes', '${x['agreements']['pending']}'),
              _card('Caixa lógico', 'R\$ ${x['ledger']['balance']}'),
            ],
            if (x == null && error == null)
              const Padding(
                padding: EdgeInsets.all(40),
                child: Center(child: CircularProgressIndicator()),
              ),
          ],
        ),
      ),
    );
  }

  Widget _card(String t, String v) =>
      Card(child: ListTile(title: Text(t), subtitle: Text(v)));
}
