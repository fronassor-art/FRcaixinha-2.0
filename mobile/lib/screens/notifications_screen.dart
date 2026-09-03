import 'package:flutter/material.dart';
import '../services/api_client.dart';
import '../services/session.dart';

class NotificationsScreen extends StatefulWidget {
  const NotificationsScreen({super.key});
  @override State<NotificationsScreen> createState() => _NotificationsScreenState();
}

class _NotificationsScreenState extends State<NotificationsScreen> {
  final api = ApiClient();
  List<dynamic> items = [];
  bool loading = true;
  String? error;

  @override void initState() { super.initState(); load(); }
  Future<void> load() async {
    api.token = await Session.getToken();
    try {
      final data = await api.get('/notifications');
      if (mounted) setState(() { items = data['items'] ?? []; loading = false; error = null; });
    } catch (e) { if (mounted) setState(() { loading = false; error = e.toString(); }); }
  }
  Future<void> read(int id) async {
    try { await api.postEmpty('/notifications/$id/read'); await load(); } catch (_) {}
  }
  Future<void> readAll() async { try { await api.postEmpty('/notifications/read-all'); await load(); } catch (_) {} }

  @override Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Notificações'), actions: [IconButton(onPressed: readAll, icon: const Icon(Icons.done_all))]),
    body: loading ? const Center(child: CircularProgressIndicator()) : error != null ? Center(child: Text(error!)) :
      RefreshIndicator(onRefresh: load, child: items.isEmpty ? ListView(children: const [SizedBox(height: 200), Center(child: Text('Nenhuma notificação.'))]) :
        ListView.builder(padding: const EdgeInsets.all(12), itemCount: items.length, itemBuilder: (_, i) {
          final n = Map<String,dynamic>.from(items[i]);
          final unread = n['read'] != true;
          return Card(child: ListTile(isThreeLine: true, leading: Icon(unread ? Icons.notifications_active : Icons.notifications_none),
            title: Text(n['title'] ?? ''), subtitle: Text('${n['message'] ?? ''}\n${n['created_at'] ?? ''}'),
            onTap: unread ? () => read(n['id'] as int) : null));
        })),
  );
}
