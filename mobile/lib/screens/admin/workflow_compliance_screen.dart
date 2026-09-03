import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';

class WorkflowComplianceScreen extends StatefulWidget {
  const WorkflowComplianceScreen({super.key});
  @override State<WorkflowComplianceScreen> createState() => _WorkflowComplianceScreenState();
}

class _WorkflowComplianceScreenState extends State<WorkflowComplianceScreen> {
  final api = ApiClient();
  Map<String, dynamic>? data;
  String? error;

  @override void initState() { super.initState(); load(); }

  Future<void> load() async {
    api.token = await Session.getToken();
    try {
      final d = await api.get('/admin/workflow-compliance');
      if (mounted) setState(() { data = d; error = null; });
    } catch (e) { if (mounted) setState(() => error = e.toString()); }
  }

  @override Widget build(BuildContext context) {
    final snapshot = (data?['snapshot'] as Map?)?.cast<String, dynamic>();
    final checks = (snapshot?['checks'] as List?)?.cast<Map<String, dynamic>>() ?? [];
    final status = data?['status'] ?? snapshot?['status'] ?? '—';
    return Scaffold(
      appBar: AppBar(title: const Text('Conformidade Operacional')),
      body: RefreshIndicator(onRefresh: load, child: ListView(padding: const EdgeInsets.all(16), children: [
        if (error != null) Text(error!),
        if (data == null && error == null) const Center(child: Padding(padding: EdgeInsets.all(40), child: CircularProgressIndicator())),
        if (data != null) ...[
          Card(child: Padding(padding: const EdgeInsets.all(16), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('Status: $status', style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 8),
            Text('Snapshot: ${data!['snapshot_date'] ?? '—'}'),
            Text('Hash: ${data!['snapshot_hash'] ?? '—'}'),
          ]))),
          const SizedBox(height: 8),
          ...checks.map((c) => Card(child: ListTile(
            leading: Icon(_icon(c['status'] as String?)),
            title: Text('${c['name']} • ${c['status']}'),
            subtitle: Text('Itens: ${c['count'] ?? 0}\n${c['details'] ?? ''}'),
          ))),
        ]
      ])),
    );
  }

  IconData _icon(String? status) => status == 'CRITICAL' ? Icons.error : status == 'ATTENTION' ? Icons.warning_amber : Icons.check_circle_outline;
}
