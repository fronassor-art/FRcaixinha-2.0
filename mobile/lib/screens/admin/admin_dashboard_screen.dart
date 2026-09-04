import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';
import 'admin_loans_screen.dart';
import 'admin_members_screen.dart';
import 'operations_dashboard_screen.dart';
import 'collections_dashboard_screen.dart';
import 'reports_dashboard_screen.dart';
import 'financial_risk_dashboard_screen.dart';

class AdminDashboardScreen extends StatefulWidget {
  const AdminDashboardScreen({super.key});
  @override State<AdminDashboardScreen> createState() => _AdminDashboardScreenState();
}

class _AdminDashboardScreenState extends State<AdminDashboardScreen> {
  final api = ApiClient();
  Map<String, dynamic>? data;
  String? error;

  @override void initState() { super.initState(); load(); }
  Future<void> load() async { api.token = await Session.getToken();
    try { data = await api.get('/admin/dashboard'); if (mounted) setState(() => error = null); }
    catch (e) { if (mounted) setState(() => error = e.toString()); }
    if (mounted) setState(() {});
  }

  String m(dynamic v) => "R\$ ${v ?? '0.00'}";
  @override Widget build(BuildContext context) {
    final d = data;
    return Scaffold(
      appBar: AppBar(title: const Text('Painel Administrativo')),
      body: RefreshIndicator(onRefresh: load, child: ListView(padding: const EdgeInsets.all(16), children: [
        if (error != null) Text(error!),
        if (d == null && error == null) const Center(child: Padding(padding: EdgeInsets.all(40), child: CircularProgressIndicator())),
        if (d != null) ...[
          _card('Caixa lógico', 'Saldo ${m(d['ledger']['balance'])}', Icons.account_balance_wallet_outlined),
          _card('Participantes', '${d['members']['active']} ativos de ${d['members']['total']}', Icons.people_outline),
          _card('Contribuições', 'Pagas ${m(d['contributions']['paid_total'])} • Pendentes ${m(d['contributions']['pending_total'])}', Icons.payments_outlined),
          _card('Empréstimos', 'Solicitados ${d['loans']['requested']} • Aprovados ${d['loans']['approved']}', Icons.request_quote_outlined),
          _card('Inadimplência', '${d['overdue_installments']} parcelas vencidas • saldo ${m(d['outstanding_loan_balance'])}', Icons.warning_amber_outlined),
          _card('Juros', 'Recebidos ${m(d['interest_received'])} • previstos ${m(d['interest_expected'])}', Icons.trending_up),
          const SizedBox(height: 8),
          ListTile(leading: const Icon(Icons.shield_outlined), title: const Text('Painel Executivo de Risco'), subtitle: const Text('Risco, alertas, respostas, CAPAs e SLAs'), trailing: const Icon(Icons.chevron_right), onTap: () => context.push('/admin/executive-risk-response')),
          ListTile(leading: const Icon(Icons.gavel_outlined), title: const Text('Centro de Decisão de Risco'), subtitle: const Text('Recomendações, justificativas e decisões auditáveis'), trailing: const Icon(Icons.chevron_right), onTap: () => context.push('/admin/executive-risk-decisions')),
          ListTile(leading: const Icon(Icons.play_circle_outline), title: const Text('Execução da Decisão'), subtitle: const Text('Responsável, evidência, verificação e encerramento'), trailing: const Icon(Icons.chevron_right), onTap: () => context.push('/admin/executive-risk-execution')),
          ListTile(leading: const Icon(Icons.verified_user_outlined), title: const Text('Governança da Decisão'), subtitle: const Text('Competência, dupla aprovação, conflitos e validação'), trailing: const Icon(Icons.chevron_right), onTap: () => context.push('/admin/executive-risk-governance')),
          ListTile(leading: const Icon(Icons.dashboard_customize_outlined), title: const Text('Centro de Controle Operacional'), subtitle: const Text('Pendências, riscos, liberações e alertas prioritários'), trailing: const Icon(Icons.chevron_right), onTap: () => context.push('/admin/operational-control')),
          ListTile(leading: const Icon(Icons.monitor_heart_outlined), title: const Text('Operação e reconciliação'), subtitle: const Text('Saldo, pagamentos, Webhooks e divergências'), trailing: const Icon(Icons.chevron_right), onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const OperationsDashboardScreen()))),
          ListTile(leading: const Icon(Icons.warning_amber_outlined), title: const Text('Cobrança e inadimplência'), subtitle: const Text('Aging, saldo vencido e eventos de cobrança'), trailing: const Icon(Icons.chevron_right), onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const CollectionsDashboardScreen()))),
          ListTile(leading: const Icon(Icons.verified_user_outlined), title: const Text('Conformidade operacional'), subtitle: const Text('SLA, execução, evidências e cadeia de integridade'), trailing: const Icon(Icons.chevron_right), onTap: () => context.push('/admin/workflow-compliance')),
          ListTile(leading: const Icon(Icons.gpp_good_outlined), title: const Text('Governança executiva'), subtitle: const Text('Saúde financeira, riscos, Ledger e reconciliação'), trailing: const Icon(Icons.chevron_right), onTap: () => context.push('/admin/governance')),
          ListTile(leading: const Icon(Icons.security_update_good_outlined), title: const Text('Risco financeiro e antifraude'), subtitle: const Text('Score explicável, revisões e bloqueios graduais'), trailing: const Icon(Icons.chevron_right), onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const FinancialRiskDashboardScreen()))),
          ListTile(leading: const Icon(Icons.assessment_outlined), title: const Text('Prestação de contas'), subtitle: const Text('Relatórios mensais, anuais e indicadores'), trailing: const Icon(Icons.chevron_right), onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const ReportsDashboardScreen()))),
          ListTile(leading: const Icon(Icons.verified_outlined), title: const Text('Fechamento da Melhoria Contínua'), subtitle: const Text('Dashboard, SLA, compliance, readiness e release v1.0'), trailing: const Icon(Icons.chevron_right), onTap: () => context.push('/admin/continuous-improvement-finalization')),
          ListTile(leading: const Icon(Icons.people), title: const Text('Participantes'), trailing: const Icon(Icons.chevron_right), onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const AdminMembersScreen()))),
          ListTile(leading: const Icon(Icons.request_quote), title: const Text('Empréstimos'), subtitle: const Text('Aprovar ou rejeitar solicitações'), trailing: const Icon(Icons.chevron_right), onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const AdminLoansScreen()))),
        ]
      ])),
    );
  }
  Widget _card(String title, String value, IconData icon) => Card(child: ListTile(leading: Icon(icon), title: Text(title), subtitle: Text(value)));
}
