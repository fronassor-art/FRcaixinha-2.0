import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:frcaixinha/models/finance_models.dart';
import 'package:frcaixinha/repositories/app_repository.dart';
import 'package:frcaixinha/screens/admin/admin_delinquency_screen.dart';
import 'package:frcaixinha/services/api_client.dart';

class FakeAdminDelinquencyRepository extends AppRepository {
  FakeAdminDelinquencyRepository({
    required this.summary,
    required this.items,
    this.summaryLoader,
    this.itemsLoader,
  }) : super(ApiClient());

  AdminDelinquencySummary summary;
  List<AdminDelinquencyItem> items;
  Future<AdminDelinquencySummary> Function()? summaryLoader;
  Future<List<AdminDelinquencyItem>> Function()? itemsLoader;
  int summaryCalls = 0;
  int itemsCalls = 0;

  @override
  Future<AdminDelinquencySummary> adminDelinquencySummary() {
    summaryCalls++;
    return summaryLoader?.call() ?? Future.value(summary);
  }

  @override
  Future<List<AdminDelinquencyItem>> adminDelinquency({
    FinancialObligationType? obligationType,
    FinancialObligationStatus? status,
    int? memberId,
    bool? overdueOnly,
    DateTime? dueFrom,
    DateTime? dueTo,
  }) {
    itemsCalls++;
    return itemsLoader?.call() ?? Future.value(items);
  }
}

AdminDelinquencySummary summary() => const AdminDelinquencySummary(
      pendingCount: 2,
      partialCount: 3,
      overdueCount: 4,
      paidCount: 1,
      totalOutstanding: '500.00',
      totalOverdue: '300.00',
      totalPartialOutstanding: '120.00',
      totalContributions: 3,
      totalInstallments: 4,
      delinquentMembersCount: 2,
      totalInterestOutstanding: '25.00',
      totalPenaltyOutstanding: '10.00',
    );

AdminDelinquencyItem contribution({
  FinancialObligationStatus status = FinancialObligationStatus.pending,
  int daysOverdue = 0,
  bool receiptAvailable = false,
}) =>
    AdminDelinquencyItem(
      memberId: 1,
      memberName: 'Ana',
      obligationType: FinancialObligationType.contribution,
      obligationId: 11,
      competence: '2026-09',
      loanId: null,
      installmentNumber: null,
      dueDate: DateTime.parse('2026-09-10'),
      amountDue: '100.00',
      amountPaid: status == FinancialObligationStatus.partial ? '40.00' : '0.00',
      outstandingAmount: status == FinancialObligationStatus.partial ? '60.00' : '100.00',
      status: status,
      daysOverdue: daysOverdue,
      principalOutstanding: '0.00',
      interestOutstanding: '0.00',
      penaltyOutstanding: '0.00',
      paymentId: 41,
      receiptAvailable: receiptAvailable,
    );

AdminDelinquencyItem installment({
  FinancialObligationStatus status = FinancialObligationStatus.overdue,
  int daysOverdue = 5,
  bool receiptAvailable = true,
}) =>
    AdminDelinquencyItem(
      memberId: 2,
      memberName: 'Bruno',
      obligationType: FinancialObligationType.loanInstallment,
      obligationId: 22,
      competence: null,
      loanId: 7,
      installmentNumber: 2,
      dueDate: DateTime.parse('2026-09-12'),
      amountDue: '130.00',
      amountPaid: '30.00',
      outstandingAmount: '100.00',
      status: status,
      daysOverdue: daysOverdue,
      principalOutstanding: '80.00',
      interestOutstanding: '15.00',
      penaltyOutstanding: '5.00',
      paymentId: 42,
      receiptAvailable: receiptAvailable,
    );

Widget screen(FakeAdminDelinquencyRepository repository) =>
    MaterialApp(home: AdminDelinquencyScreen(repository: repository));

void main() {
  testWidgets('shows initial loading while summary and list are pending',
      (tester) async {
    final summaryCompleter = Completer<AdminDelinquencySummary>();
    final itemsCompleter = Completer<List<AdminDelinquencyItem>>();
    final repository = FakeAdminDelinquencyRepository(
      summary: summary(),
      items: const [],
      summaryLoader: () => summaryCompleter.future,
      itemsLoader: () => itemsCompleter.future,
    );

    await tester.pumpWidget(screen(repository));

    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    expect(repository.summaryCalls, 1);
    expect(repository.itemsCalls, 1);

    summaryCompleter.complete(summary());
    itemsCompleter.complete(const []);
    await tester.pumpAndSettle();
  });

  testWidgets('shows error and retry reloads summary and list', (tester) async {
    var fail = true;
    final repository = FakeAdminDelinquencyRepository(
      summary: summary(),
      items: const [],
      summaryLoader: () => fail
          ? Future.error(StateError('falha de resumo'))
          : Future.value(summary()),
      itemsLoader: () => fail
          ? Future.error(StateError('falha de lista'))
          : Future.value(const []),
    );

    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();

    expect(find.text('Tentar novamente'), findsOneWidget);
    expect(find.textContaining('Não foi possível carregar a inadimplência.'),
        findsOneWidget);

    fail = false;
    await tester.tap(find.text('Tentar novamente'));
    await tester.pumpAndSettle();

    expect(find.text('Nenhuma obrigação em aberto.'), findsOneWidget);
    expect(repository.summaryCalls, 2);
    expect(repository.itemsCalls, 2);
  });

  testWidgets('shows empty list after a successful load', (tester) async {
    final repository =
        FakeAdminDelinquencyRepository(summary: summary(), items: const []);

    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();

    expect(find.text('Nenhuma obrigação em aberto.'), findsOneWidget);
  });

  testWidgets('shows summary and contribution and installment financial data',
      (tester) async {
    final repository = FakeAdminDelinquencyRepository(
      summary: summary(),
      items: [
        contribution(),
        contribution(status: FinancialObligationStatus.partial),
        installment(),
      ],
    );

    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();

    expect(find.text('Participantes inadimplentes'), findsOneWidget);
    expect(
      find.descendant(
        of: find.ancestor(
            of: find.text('Participantes inadimplentes'), matching: find.byType(SizedBox)),
        matching: find.text('2'),
      ),
      findsOneWidget,
    );
    expect(find.text('R\$ 500.00'), findsOneWidget);
    expect(find.text('R\$ 300.00'), findsOneWidget);
    expect(find.text('R\$ 25.00'), findsOneWidget);
    expect(find.text('R\$ 10.00'), findsOneWidget);
    expect(find.text('Pendentes'), findsOneWidget);
    expect(find.text('Parciais'), findsOneWidget);
    expect(find.text('Em atraso'), findsOneWidget);

    expect(find.text('Ana'), findsNWidgets(2));
    expect(find.text('Competência: 2026-09'), findsNWidgets(2));
    expect(find.textContaining('Estado: Pendente'), findsOneWidget);
    expect(find.textContaining('Estado: Parcial'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.text('Bruno'),
      300,
      scrollable: find.byType(Scrollable).first,
    );
    expect(find.text('Bruno'), findsOneWidget);
    expect(find.text('Empréstimo #7 • Parcela 2'), findsOneWidget);
    expect(find.textContaining('Estado: Em atraso'), findsOneWidget);
    expect(find.text('5 dias em atraso'), findsOneWidget);
    expect(find.text('Principal pendente: R\$ 80.00'), findsOneWidget);
    expect(find.text('Juros pendentes: R\$ 15.00'), findsOneWidget);
    expect(find.text('Multa pendente: R\$ 5.00'), findsOneWidget);
    expect(find.text('Recibo disponível'), findsOneWidget);
  });

  testWidgets('pull to refresh reloads summary and list', (tester) async {
    final repository = FakeAdminDelinquencyRepository(
      summary: summary(),
      items: [contribution()],
    );

    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();
    expect(repository.summaryCalls, 1);
    expect(repository.itemsCalls, 1);

    await tester.fling(find.byType(ListView), const Offset(0, 300), 1000);
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();

    expect(repository.summaryCalls, 2);
    expect(repository.itemsCalls, 2);
  });
}
