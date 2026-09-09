import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';

class ReportsDashboardScreen extends StatefulWidget {
  const ReportsDashboardScreen({super.key});
  @override
  State<ReportsDashboardScreen> createState() => _ReportsDashboardScreenState();
}

class _ReportsDashboardScreenState extends State<ReportsDashboardScreen> {
  final api = ApiClient();
  Map<String, dynamic>? monthly;
  Map<String, dynamic>? annual;
  String? error;
  final year = DateTime.now().year;
  Future<void> load() async {
    api.token = await Session.getToken();
    try {
      monthly = await api.get(
        '/admin/reports/accountability/monthly?competence=${DateTime.now().toIso8601String().substring(0, 10)}',
      );
      annual = await api.get('/admin/reports/accountability/annual?year=$year');
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

  String v(dynamic x) =>
      "R" + String.fromCharCode(36) + " " + (x ?? "0.00").toString();
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Prestação de contas'),
        actions: [IconButton(onPressed: load, icon: const Icon(Icons.refresh))],
      ),
      body: RefreshIndicator(
        onRefresh: load,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (error != null) Text(error!),
            if (monthly != null) ...[
              const Text(
                'Competência atual',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
              ),
              _card(
                'Contribuições pagas',
                v(monthly!['inflows']['contributions_paid']),
              ),
              _card(
                'Juros recebidos',
                v(monthly!['inflows']['interest_received']),
              ),
              _card(
                'Penalidades recebidas',
                v(monthly!['inflows']['penalties_received']),
              ),
              _card('Despesas', v(monthly!['expenses'])),
              _card('Resultado operacional', v(monthly!['operating_result'])),
              _card('Saldo líquido do Ledger', v(monthly!['ledger']['net'])),
            ],
            if (annual != null) ...[
              const SizedBox(height: 12),
              Text(
                'Acumulado $year',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
              ),
              _card(
                'Contribuições',
                v(annual!['totals']['contributions_paid']),
              ),
              _card('Juros', v(annual!['totals']['interest_received'])),
              _card('Despesas', v(annual!['totals']['expenses'])),
              _card('Resultado', v(annual!['totals']['operating_result'])),
            ],
            if (monthly == null && annual == null && error == null)
              const Padding(
                padding: EdgeInsets.all(40),
                child: Center(child: CircularProgressIndicator()),
              ),
          ],
        ),
      ),
    );
  }

  Widget _card(String t, String val) =>
      Card(child: ListTile(title: Text(t), subtitle: Text(val)));
}
