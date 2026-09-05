import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';

class AdminContributionsScreen extends StatefulWidget {
  const AdminContributionsScreen({super.key});

  @override
  State<AdminContributionsScreen> createState() =>
      _AdminContributionsScreenState();
}

class _AdminContributionsScreenState
    extends State<AdminContributionsScreen> {
  final api = ApiClient();
  List<dynamic> items = [];
  String? error;
  bool loading = true;

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    api.token = await Session.getToken();

    if (mounted) {
      setState(() {
        loading = true;
        error = null;
      });
    }

    try {
      final r = await api.get('/admin/contributions');
      if (mounted) {
        setState(() {
          items = r['items'] ?? [];
          error = null;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          error = e.toString();
        });
      }
    } finally {
      if (mounted) {
        setState(() {
          loading = false;
        });
      }
    }
  }

  void showDetails(dynamic x) {
    showDialog(
      context: context,
      builder: (_) => AlertDialog(
        title: Text(x['member_name'] ?? 'Contribuição'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('ID: ${x['id']}'),
            Text('Participante: ${x['member_name'] ?? '-'}'),
            Text('Competência: ${x['competence'] ?? '-'}'),
            Text('Valor: R\$ ${x['amount'] ?? '0.00'}'),
            Text('Status: ${x['status'] ?? '-'}'),
            Text('Pagamento: ${x['payment_id'] ?? 'Não registrado'}'),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Fechar'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Contribuições'),
        actions: [
          IconButton(
            onPressed: load,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: load,
        child: ListView(
          padding: const EdgeInsets.all(12),
          children: [
            if (error != null)
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Text(error!),
                ),
              ),
            if (loading)
              const Padding(
                padding: EdgeInsets.all(40),
                child: Center(
                  child: CircularProgressIndicator(),
                ),
              ),
            if (!loading && error == null && items.isEmpty)
              const Card(
                child: Padding(
                  padding: EdgeInsets.all(20),
                  child: Center(
                    child: Text('Nenhuma contribuição registrada.'),
                  ),
                ),
              ),
            ...items.map(
              (x) => Card(
                child: ListTile(
                  title: Text(x['member_name'] ?? 'Participante'),
                  subtitle: Text(
                    'R\$ ${x['amount']} • ${x['competence']} • ${x['status']}',
                  ),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => showDetails(x),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
