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

  void _showDetails(String title, String value, {String? description}) {
    showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(title),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              value,
              style: const TextStyle(
                fontSize: 22,
                fontWeight: FontWeight.bold,
              ),
            ),
            if (description != null) ...[
              const SizedBox(height: 12),
              Text(description),
            ],
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
    final x = d;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Governança executiva'),
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
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => _showDetails(
                    'Status da governança',
                    '${x['status']}',
                    description: (x['risk_flags'] as List).isEmpty
                        ? 'Não existem alertas críticos.'
                        : 'Alertas: ${(x['risk_flags'] as List).join(', ')}',
                  ),
                ),
              ),
              _card(
                'Integridade do Ledger',
                x['checks']['ledger_integrity'] ? 'PASS' : 'ATENÇÃO',
                () => _showDetails(
                  'Integridade do Ledger',
                  x['checks']['ledger_integrity'] ? 'PASS' : 'ATENÇÃO',
                  description:
                      'Verificação da integridade e continuidade dos lançamentos financeiros.',
                ),
              ),
              _card(
                'Última reconciliação',
                x['checks']['reconciliation_latest'] ? 'PASS' : 'ATENÇÃO',
                () => _showDetails(
                  'Última reconciliação',
                  x['checks']['reconciliation_latest'] ? 'PASS' : 'ATENÇÃO',
                  description: x['latest_reconciliation'] == null
                      ? 'Ainda não existe uma reconciliação financeira registrada.'
                      : 'Existe uma reconciliação financeira registrada.',
                ),
              ),
              _card(
                'Webhooks pendentes',
                '${x['webhooks']['pending']}',
                () => _showDetails(
                  'Webhooks pendentes',
                  '${x['webhooks']['pending']}',
                  description:
                      'Quantidade de eventos de integração aguardando processamento.',
                ),
              ),
              _card(
                'Inadimplência',
                '${x['collections']['overdue_installments']} parcelas • R\$ ${x['collections']['overdue_balance']}',
                () => _showDetails(
                  'Inadimplência',
                  '${x['collections']['overdue_installments']} parcelas',
                  description:
                      'Saldo vencido: R\$ ${x['collections']['overdue_balance']}',
                ),
              ),
              _card(
                'Acordos pendentes',
                '${x['agreements']['pending']}',
                () => _showDetails(
                  'Acordos pendentes',
                  '${x['agreements']['pending']}',
                  description:
                      'Quantidade de acordos de cobrança aguardando processamento.',
                ),
              ),
              _card(
                'Caixa lógico',
                'R\$ ${x['ledger']['balance']}',
                () => _showDetails(
                  'Caixa lógico',
                  'R\$ ${x['ledger']['balance']}',
                  description:
                      'Créditos: R\$ ${x['ledger']['credits']} • Débitos: R\$ ${x['ledger']['debits']}',
                ),
              ),
            ],
            if (x == null && error == null)
              const Padding(
                padding: EdgeInsets.all(40),
                child: Center(
                  child: CircularProgressIndicator(),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _card(String title, String value, VoidCallback onTap) {
    return Card(
      child: ListTile(
        title: Text(title),
        subtitle: Text(value),
        trailing: const Icon(Icons.chevron_right),
        onTap: onTap,
      ),
    );
  }
}
