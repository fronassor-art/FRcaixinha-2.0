import "package:provider/provider.dart";
import 'package:flutter/material.dart';
import '../services/api_client.dart';
import '../services/session.dart';
import 'package:go_router/go_router.dart';
import '../app.dart';
import 'notifications_screen.dart';
import '../theme/frcaixinha_theme.dart';
import '../widgets/fr_brand_mark.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final api = ApiClient();
  Map<String, dynamic>? profile, summary;
  String? error;
  bool menuOpen = false;
  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    api.token = await Session.getToken();
    try {
      final p = await api.get('/members/me');
      Map<String, dynamic>? s;
      if (p['member'] != null) s = await api.get('/contributions/summary');
      if (mounted)
        setState(() {
          profile = p;
          summary = s;
          error = null;
        });
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    }
  }

  Future<void> logout() async {
    await context.read<AppState>().logout();
    if (mounted) context.go('/login');
  }

  @override
  Widget build(BuildContext context) {
    final member = profile?['member'];
    return Scaffold(
      appBar: AppBar(
        title: const Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            FRBrandMark(size: 36),
            SizedBox(width: 10),
            Text('FRcaixinha'),
          ],
        ),
        actions: [
          IconButton(
            tooltip: 'Notificações',
            onPressed:
                () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => const NotificationsScreen(),
                  ),
                ),
            icon: const Icon(Icons.notifications_none),
          ),
          IconButton(
            tooltip: 'Sair',
            onPressed: logout,
            icon: const Icon(Icons.logout),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: load,
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 680),
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.all(20),
              children: [
                Text(
                  menuOpen
                      ? 'Menu'
                      : profile == null
                      ? error == null
                          ? 'Carregando...'
                          : 'Não foi possível carregar'
                      : 'Olá, ${profile!['name']}',
                  style: Theme.of(context).textTheme.headlineMedium,
                ),
                const SizedBox(height: 8),
                Text(
                  menuOpen
                      ? 'Acesse os recursos da sua conta.'
                      : 'Acompanhe sua caixinha em um só lugar.',
                  style: const TextStyle(color: FRColors.muted),
                ),
                if (error != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          error!,
                          style: TextStyle(
                            color: Theme.of(context).colorScheme.error,
                          ),
                        ),
                        TextButton(
                          onPressed: load,
                          child: const Text('Tentar novamente'),
                        ),
                      ],
                    ),
                  ),
                if (!menuOpen && summary != null)
                  Card(
                    color: FRColors.elevated,
                    child: Padding(
                      padding: const EdgeInsets.all(20),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text(
                            'RESUMO DE CONTRIBUIÇÕES',
                            style: TextStyle(
                              color: FRColors.muted,
                              fontSize: 12,
                              fontWeight: FontWeight.w700,
                              letterSpacing: 1.2,
                            ),
                          ),
                          const SizedBox(height: 16),
                          Text(
                            'Contribuições pagas: R\$ ${summary!['paid_total']}',
                            style: Theme.of(context).textTheme.titleLarge,
                          ),
                          Text('Pendentes: R\$ ${summary!['pending_total']}'),
                          Text('Cotas: ${summary!['quota_units']}'),
                        ],
                      ),
                    ),
                  ),
                const SizedBox(height: 24),
                Text(
                  menuOpen ? 'Todos os acessos' : 'Acessos rápidos',
                  style: Theme.of(context).textTheme.titleLarge,
                ),
                const SizedBox(height: 12),
                if (menuOpen && profile?['role'] == 'ADMIN')
                  Card(
                    child: ListTile(
                      leading: const Icon(Icons.admin_panel_settings),
                      title: const Text('Painel Administrativo'),
                      subtitle: const Text(
                        'Gestão, empréstimos, caixa e auditoria',
                      ),
                      trailing: const Icon(Icons.chevron_right),
                      onTap: () => context.push('/admin'),
                    ),
                  ),
                if (member != null)
                  Card(
                    child: ListTile(
                      leading: const Icon(Icons.payments_outlined),
                      title: const Text('Contribuições'),
                      subtitle: const Text('Ver competências e pagar com Pix'),
                      trailing: const Icon(Icons.chevron_right),
                      onTap: () => context.push('/contributions'),
                    ),
                  ),
                if (member != null)
                  Card(
                    child: ListTile(
                      leading: const Icon(
                        Icons.account_balance_wallet_outlined,
                      ),
                      title: const Text('Minhas obrigações'),
                      subtitle: const Text('Acompanhe vencimentos e saldos.'),
                      trailing: const Icon(Icons.chevron_right),
                      onTap: () => context.push('/obligations'),
                    ),
                  ),
                Card(
                  child: ListTile(
                    leading: const Icon(Icons.request_quote_outlined),
                    title: const Text('Empréstimos'),
                    subtitle: const Text('Solicite e acompanhe suas parcelas.'),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => context.push('/loans'),
                  ),
                ),
                Card(
                  child: ListTile(
                    leading: const Icon(Icons.receipt_long_outlined),
                    title: const Text('Extrato'),
                    subtitle: const Text(
                      'Consulte os lançamentos da sua conta.',
                    ),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => context.push('/statement'),
                  ),
                ),
                if (menuOpen)
                  Card(
                    child: ListTile(
                      leading: const Icon(
                        Icons.account_balance_wallet_outlined,
                      ),
                      title: const Text('Minha prestação de contas'),
                      subtitle: const Text(
                        'Veja apenas os seus dados financeiros.',
                      ),
                      trailing: const Icon(Icons.chevron_right),
                      onTap: () => context.push('/member-portal'),
                    ),
                  ),
                if (menuOpen)
                  Card(
                    child: ListTile(
                      leading: const Icon(Icons.notifications_none),
                      title: const Text('Notificações'),
                      subtitle: const Text(
                        'Avisos sobre vencimentos e pagamentos.',
                      ),
                      trailing: const Icon(Icons.chevron_right),
                      onTap:
                          () => Navigator.push(
                            context,
                            MaterialPageRoute(
                              builder: (_) => const NotificationsScreen(),
                            ),
                          ),
                    ),
                  ),
                if (menuOpen)
                  Card(
                    child: ListTile(
                      leading: const Icon(Icons.tune),
                      title: const Text('Central de comunicação'),
                      subtitle: const Text('Escolha canais e tipos de aviso.'),
                      trailing: const Icon(Icons.chevron_right),
                      onTap: () => context.push('/communications'),
                    ),
                  ),
                if (menuOpen)
                  Card(
                    child: ListTile(
                      leading: const Icon(Icons.privacy_tip_outlined),
                      title: const Text('Privacidade e LGPD'),
                      subtitle: const Text(
                        'Consulte seus dados e solicitações de privacidade.',
                      ),
                      trailing: const Icon(Icons.chevron_right),
                      onTap: () => context.push('/privacy'),
                    ),
                  ),
              ],
            ),
          ),
        ),
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: menuOpen ? 1 : 0,
        onDestinationSelected: (index) => setState(() => menuOpen = index == 1),
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.home_outlined),
            selectedIcon: Icon(Icons.home),
            label: 'Início',
          ),
          NavigationDestination(
            icon: Icon(Icons.grid_view_outlined),
            selectedIcon: Icon(Icons.grid_view),
            label: 'Menu',
          ),
        ],
      ),
    );
  }
}
