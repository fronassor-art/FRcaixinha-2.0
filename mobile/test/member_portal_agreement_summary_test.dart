import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:frcaixinha/app.dart';
import 'package:frcaixinha/screens/member_portal_screen.dart';
import 'package:frcaixinha/services/api_client.dart';
import 'package:provider/provider.dart';

class FakeMemberPortalApi extends ApiClient {
  FakeMemberPortalApi(this.response);

  final Map<String, dynamic> response;
  final paths = <String>[];

  @override
  Future<Map<String, dynamic>> get(String path) async {
    paths.add(path);
    return response;
  }
}

Map<String, dynamic> dashboard({
  int overdueInstallments = 1,
  bool includeAgreementSummary = true,
}) => {
  'member': {'name': 'Participante H4-C6'},
  'summary': {
    'contributions_paid': '100.00',
    'loan_payments': '25.00',
    'loan_outstanding': '0.00',
    'overdue_balance': '0.00',
    'overdue_installments': 0,
    'on_time_ratio': 1.0,
    if (includeAgreementSummary) ...{
      'agreement_payments': '43.00',
      'agreement_outstanding': '67.00',
      'agreement_overdue_balance': '67.00',
      'agreement_overdue_installments': overdueInstallments,
    },
  },
  'installments': [],
  'agreements': [],
};

Widget screen(FakeMemberPortalApi api) => ChangeNotifierProvider.value(
  value: AppState(),
  child: MaterialApp(home: MemberPortalScreen(apiClient: api)),
);

void main() {
  testWidgets('renders Agreement summary independently from Loan values', (
    tester,
  ) async {
    final api = FakeMemberPortalApi(dashboard());

    await tester.pumpWidget(screen(api));
    await tester.pumpAndSettle();

    expect(find.text('Pagamentos de acordos: R\$ 43.00'), findsOneWidget);
    expect(find.text('Saldo de acordos: R\$ 67.00'), findsOneWidget);
    expect(find.text('Acordos em atraso: R\$ 67.00'), findsOneWidget);
    expect(find.text('1 parcela em atraso'), findsOneWidget);
    expect(find.text('Pagamentos de empréstimos: R\$ 25.00'), findsOneWidget);
    expect(find.text('Saldo de empréstimos: R\$ 0.00'), findsOneWidget);
    expect(api.paths, ['/member-portal/dashboard']);
  });

  testWidgets('pluralizes multiple overdue Agreement installments', (
    tester,
  ) async {
    await tester.pumpWidget(
      screen(FakeMemberPortalApi(dashboard(overdueInstallments: 2))),
    );
    await tester.pumpAndSettle();

    expect(find.text('2 parcelas em atraso'), findsOneWidget);
  });

  testWidgets('uses zero fallback when Agreement summary fields are absent', (
    tester,
  ) async {
    await tester.pumpWidget(
      screen(FakeMemberPortalApi(dashboard(includeAgreementSummary: false))),
    );
    await tester.pumpAndSettle();

    expect(find.text('Pagamentos de acordos: R\$ 0.00'), findsOneWidget);
    expect(find.text('Saldo de acordos: R\$ 0.00'), findsOneWidget);
    expect(find.text('Acordos em atraso: R\$ 0.00'), findsOneWidget);
    expect(find.text('0 parcelas em atraso'), findsOneWidget);
  });
}
