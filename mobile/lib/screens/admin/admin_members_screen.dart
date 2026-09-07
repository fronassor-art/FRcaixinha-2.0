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
  List<dynamic> pendingUsers = [];
  List<dynamic> groups = [];

  String? error;
  bool loading = false;

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
      final membersResponse = await api.get('/admin/members');
      final pendingResponse = await api.get('/admin/pending-users');
      final groupsResponse = await api.get('/admin/groups');

      items = membersResponse['items'] ?? [];
      pendingUsers = pendingResponse['items'] ?? [];
      groups = groupsResponse['items'] ?? [];
    } catch (e) {
      if (mounted) {
        setState(() {
          error = e.toString();
        });
      }
    }

    if (mounted) {
      setState(() {
        loading = false;
      });
    }
  }

  Future<void> assignUser(dynamic user) async {
    if (groups.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Nenhum grupo ativo disponível.')),
      );
      return;
    }

    dynamic selectedGroup;

    selectedGroup = await showDialog<dynamic>(
      context: context,
      builder:
          (_) => AlertDialog(
            title: const Text('Vincular participante'),
            content: DropdownButtonFormField<dynamic>(
              decoration: const InputDecoration(
                labelText: 'Grupo',
                border: OutlineInputBorder(),
              ),
              items:
                  groups.map<DropdownMenuItem<dynamic>>((group) {
                    return DropdownMenuItem<dynamic>(
                      value: group,
                      child: Text(
                        '${group['name']} - R\$ ${group['monthly_amount']}',
                      ),
                    );
                  }).toList(),
              onChanged: (value) {
                Navigator.pop(context, value);
              },
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context),
                child: const Text('Cancelar'),
              ),
            ],
          ),
    );

    if (selectedGroup == null) {
      return;
    }

    try {
      api.token = await Session.getToken();

      await api.post(
        '/members/assign/${user['id']}/${selectedGroup['id']}',
        {},
      );

      if (!mounted) return;

      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            '${user['name']} foi vinculado ao grupo ${selectedGroup['name']}.',
          ),
        ),
      );

      await load();
    } catch (e) {
      if (!mounted) return;

      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('Erro ao vincular: $e')));
    }
  }

  void showMemberDetails(dynamic x) {
    showDialog(
      context: context,
      builder:
          (_) => AlertDialog(
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

  Widget buildPendingSection() {
    if (pendingUsers.isEmpty) {
      return Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Row(
            children: [
              const Icon(Icons.check_circle_outline),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  'Nenhum cadastro pendente.',
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
              ),
            ],
          ),
        ),
      );
    }

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.pending_actions),
                const SizedBox(width: 8),
                Text(
                  'Cadastros pendentes (${pendingUsers.length})',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              ],
            ),
            const SizedBox(height: 8),
            const Text(
              'Usuários que criaram uma conta, mas ainda não foram vinculados a um grupo.',
            ),
            const SizedBox(height: 12),
            ...pendingUsers.map(
              (user) => Card(
                color: Theme.of(context).colorScheme.surfaceContainerHighest,
                child: ListTile(
                  leading: const CircleAvatar(
                    child: Icon(Icons.person_add_alt_1),
                  ),
                  title: Text(user['name'] ?? ''),
                  subtitle: Text(
                    '${user['email'] ?? ''}\n'
                    'CPF: ${user['cpf'] ?? '-'}',
                  ),
                  isThreeLine: true,
                  trailing: OutlinedButton(
                    onPressed: () => assignUser(user),
                    child: const Text('Vincular'),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Participantes'),
        actions: [
          IconButton(
            onPressed: loading ? null : load,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: load,
        child: ListView(
          padding: const EdgeInsets.all(12),
          children: [
            if (loading)
              const Padding(
                padding: EdgeInsets.only(bottom: 12),
                child: LinearProgressIndicator(),
              ),
            if (error != null)
              Padding(padding: const EdgeInsets.all(12), child: Text(error!)),
            buildPendingSection(),
            const SizedBox(height: 16),
            Text(
              'Participantes ativos',
              style: Theme.of(context).textTheme.titleLarge,
            ),
            const SizedBox(height: 8),
            if (items.isEmpty)
              const Card(
                child: Padding(
                  padding: EdgeInsets.all(16),
                  child: Text('Nenhum participante vinculado.'),
                ),
              ),
            ...items.map(
              (x) => Card(
                child: ListTile(
                  leading: const CircleAvatar(child: Icon(Icons.person)),
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
