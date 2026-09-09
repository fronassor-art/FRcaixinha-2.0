import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';

class ExecutiveDashboardScreen extends StatefulWidget {
  const ExecutiveDashboardScreen({super.key});
  @override
  State<ExecutiveDashboardScreen> createState() =>
      _ExecutiveDashboardScreenState();
}

class _ExecutiveDashboardScreenState extends State<ExecutiveDashboardScreen> {
  final api = ApiClient();
  Map<String, dynamic>? d;
  String? error;
  Future<void> load() async {
    api.token = await Session.getToken();
    try {
      d = await api.get('/admin/executive-dashboard');
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
        title: const Text('Painel executivo'),
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
                        ? 'Sem alertas'
                        : 'Alertas: ${(x['risk_flags'] as List).join(', ')}',
                  ),
                ),
              ),
              _card('Caixa lógico', 'R\$ ${x["cash"]["logical_balance"]}'),
              _card(
                'Contribuições pagas',
                'R\$ ${x["contributions"]["paid_total"]}',
              ),
              _card(
                'Empréstimos ativos',
                '${x["loans"]["active_or_restructured"]} • Recebível R\$ ${x["loans"]["receivable"]}',
              ),
              _card(
                'Inadimplência',
                '${x["collections"]["overdue_installments"]} parcelas • R\$ ${x["collections"]["overdue_balance"]}',
              ),
              _card(
                'Recuperação',
                '${x['recovery']['open_cases']} casos • ${x['recovery']['promises_due_or_late']} promessas vencidas/para ação',
              ),
              _card(
                'Risco',
                '${x['risk']['review']} em revisão • ${x['risk']['blocked']} bloqueadas',
              ),
              _card(
                'Resultado operacional (proxy)',
                'R\$ ${x["profitability"]["operating_result_proxy"]}',
              ),
              _card(
                'Reconciliação',
                x['reconciliation'] == null
                    ? 'Sem reconciliação registrada'
                    : x['reconciliation']['status'],
              ),
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
