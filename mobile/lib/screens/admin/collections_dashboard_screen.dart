import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';

class CollectionsDashboardScreen extends StatefulWidget {
  const CollectionsDashboardScreen({super.key});
  @override
  State<CollectionsDashboardScreen> createState() =>
      _CollectionsDashboardScreenState();
}

class _CollectionsDashboardScreenState
    extends State<CollectionsDashboardScreen> {
  final api = ApiClient();
  Map<String, dynamic>? d;
  String? error;
  Future<void> load() async {
    api.token = await Session.getToken();
    try {
      d = await api.get('/admin/collections/summary');
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

  String money(dynamic v) => "R\$ ${v ?? "0.00"}";
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Cobrança e inadimplência'),
        actions: [IconButton(onPressed: load, icon: const Icon(Icons.refresh))],
      ),
      body: RefreshIndicator(
        onRefresh: load,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (error != null) Text(error!),
            if (d != null) ...[
              _card(
                'Parcelas vencidas',
                '${d!['overdue_installments']}',
                Icons.warning_amber_outlined,
              ),
              _card(
                'Saldo em atraso',
                money(d!['overdue_balance']),
                Icons.payments_outlined,
              ),
              const SizedBox(height: 8),
              const Text(
                'Aging',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
              ),
              ...Map<String, dynamic>.from(d!['aging']).entries.map(
                (e) => ListTile(
                  title: Text(e.key + ' dias'),
                  trailing: Text(money(e.value)),
                ),
              ),
            ],
            if (d == null && error == null)
              const Padding(
                padding: EdgeInsets.all(40),
                child: Center(child: CircularProgressIndicator()),
              ),
          ],
        ),
      ),
    );
  }

  Widget _card(String t, String v, IconData i) => Card(
    child: ListTile(leading: Icon(i), title: Text(t), subtitle: Text(v)),
  );
}
