import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import '../../app.dart';

class LoansScreen extends StatefulWidget {
  const LoansScreen({super.key});
  @override
  State<LoansScreen> createState() => _LoansScreenState();
}

class _LoansScreenState extends State<LoansScreen> {
  List<dynamic> items = [];
  bool loading = true;
  String? error;
  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    try {
      final r = await context.read<AppState>().repository.loans();
      if (mounted) setState(() => items = r['items'] as List<dynamic>? ?? []);
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('Empréstimos'),
      actions: [
        IconButton(
          onPressed: () => context.push('/loans/request'),
          icon: const Icon(Icons.add),
        ),
      ],
    ),
    body: RefreshIndicator(
      onRefresh: load,
      child:
          loading
              ? const Center(child: CircularProgressIndicator())
              : error != null
              ? Center(child: Text(error!))
              : items.isEmpty
              ? const Center(child: Text('Nenhum empréstimo encontrado.'))
              : ListView.builder(
                itemCount: items.length,
                itemBuilder: (_, i) {
                  final l = items[i] as Map<String, dynamic>;
                  return Card(
                    margin: const EdgeInsets.symmetric(
                      horizontal: 12,
                      vertical: 6,
                    ),
                    child: ListTile(
                      title: Text('Empréstimo #${l['id']}'),
                      subtitle: Text(
                        'Principal: R\$ ${l['principal']} • ${l['status']}',
                      ),
                      trailing: const Icon(Icons.chevron_right),
                      onTap: () => context.push('/loans/${l['id']}'),
                    ),
                  );
                },
              ),
    ),
  );
}
