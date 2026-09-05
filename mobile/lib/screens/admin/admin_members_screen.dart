import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';

class AdminMembersScreen extends StatefulWidget {
  const AdminMembersScreen({super.key});

  @override
  State<AdminMembersScreen> createState() => _AdminMembersScreenState();
}

class _AdminMembersScreenState extends State<AdminMembersScreen> {
  final api = ApiClient();
  List<dynamic> items = [];
  String? error;

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    api.token = await Session.getToken();

    try {
      final r = await api.get('/admin/members');
      items = r['items'] ?? [];

      if (mounted) {
        setState(() => error = null);
      }
    } catch (e) {
      if (mounted) {
        setState(() => error = e.toString());
      }
    }

    if (mounted) {
      setState(() {});
    }
  }

  void showMemberDetails(dynamic x) {
    showDialog(
      context: context,
      builder: (_) => AlertDialog(
        title: Text(x['user']['name'] ?? 'Participante'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('E-mail: ${x['user']['email'] ?? ''}'),
            const SizedBox(height: 8),
            Text('Status: ${x['status']}'),
            const SizedBox(height: 8),
            Text('Cotas: ${x['quota_units']}'),
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
        title: const Text('Participantes'),
      ),
      body: RefreshIndicator(
        onRefresh: load,
        child: ListView(
          padding: const EdgeInsets.all(12),
          children: [
            if (error != null)
              Padding(
                padding: const EdgeInsets.all(12),
                child: Text(error!),
              ),
            ...items.map(
              (x) => Card(
                child: ListTile(
                  leading: const CircleAvatar(
                    child: Icon(Icons.person),
                  ),
                  title: Text(x['user']['name'] ?? ''),
                  subtitle: Text(
                    '${x['user']['email'] ?? ''}\n'
                    'Status: ${x['status']} • Cotas: ${x['quota_units']}',
                  ),
                  isThreeLine: true,
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => showMemberDetails(x),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
