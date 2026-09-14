import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:frcaixinha/models/finance_models.dart';
import 'package:frcaixinha/repositories/app_repository.dart';
import 'package:frcaixinha/screens/admin/admin_delinquency_screen.dart';
import 'package:frcaixinha/screens/payments/pix_receipt_screen.dart';
import 'package:frcaixinha/services/api_client.dart';

class AdminDelinquencyCall {
  const AdminDelinquencyCall({
    this.obligationType,
    this.status,
    this.memberId,
    this.overdueOnly,
    this.dueFrom,
    this.dueTo,
  });

  final FinancialObligationType? obligationType;
  final FinancialObligationStatus? status;
  final int? memberId;
  final bool? overdueOnly;
  final DateTime? dueFrom;
  final DateTime? dueTo;
}

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
  final calls = <AdminDelinquencyCall>[];
  final receiptPaymentIds = <int>[];
  int statusCalls = 0;

  @override
  Future<PixReceipt> paymentReceipt(int paymentId) async {
    receiptPaymentIds.add(paymentId);
    return PixReceipt.fromJson({
      'receipt_number': 'PIX-$paymentId',
      'payment': {'id': paymentId},
    });
  }

  @override
  Future<PixPaymentStatusDetails> paymentStatus(int paymentId) async {
    statusCalls++;
    throw StateError('Receipt viewing must not poll payment status');
  }


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
    calls.add(AdminDelinquencyCall(
      obligationType: obligationType,
      status: status,
      memberId: memberId,
      overdueOnly: overdueOnly,
      dueFrom: dueFrom,
      dueTo: dueTo,
    ));
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
  int? paymentId = 41,
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
      paymentId: paymentId,
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

Future<void> openFilters(WidgetTester tester) async {
  await tester.tap(find.text('Filtros'));
  await tester.pumpAndSettle();
}

Future<void> selectFilterOption(
  WidgetTester tester,
  Key field,
  Key option,
) async {
  await tester.tap(find.byKey(field));
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(option));
  await tester.pumpAndSettle();
}

Future<void> selectFilterDate(
  WidgetTester tester,
  Key field,
  String date,
) async {
  await tester.tap(find.byKey(field));
  await tester.pumpAndSettle();
  final material3Edit = find.byIcon(Icons.edit_outlined);
  await tester.tap(
    material3Edit.evaluate().isNotEmpty ? material3Edit : find.byIcon(Icons.edit),
  );
  await tester.pumpAndSettle();
  await tester.enterText(find.byType(TextFormField), date);
  await tester.tap(find.text('OK'));
  await tester.pumpAndSettle();
}

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

  testWidgets('applies contribution filter without reloading global summary',
      (tester) async {
    final repository = FakeAdminDelinquencyRepository(
      summary: summary(),
      items: [contribution()],
    );

    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();
    await openFilters(tester);
    await selectFilterOption(
      tester,
      const Key('delinquency-filter-type'),
      const Key('delinquency-filter-type-contribution'),
    );
    await tester.tap(find.byKey(const Key('delinquency-filter-apply')));
    await tester.pumpAndSettle();

    expect(repository.summaryCalls, 1);
    expect(repository.itemsCalls, 2);
    final call = repository.calls.last;
    expect(call.obligationType, FinancialObligationType.contribution);
    expect(call.status, isNull);
    expect(call.overdueOnly, isNull);
    expect(call.dueFrom, isNull);
    expect(call.dueTo, isNull);
  });

  testWidgets('applies installment overdue filters with due period',
      (tester) async {
    final repository = FakeAdminDelinquencyRepository(
      summary: summary(),
      items: [installment()],
    );

    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();
    await openFilters(tester);
    await selectFilterOption(
      tester,
      const Key('delinquency-filter-type'),
      const Key('delinquency-filter-type-installment'),
    );
    await selectFilterOption(
      tester,
      const Key('delinquency-filter-status'),
      const Key('delinquency-filter-status-overdue'),
    );
    await tester.tap(find.byKey(const Key('delinquency-filter-overdue')));
    await tester.pumpAndSettle();
    await selectFilterDate(
      tester,
      const Key('delinquency-filter-due-from'),
      '09/10/2026',
    );
    await selectFilterDate(
      tester,
      const Key('delinquency-filter-due-to'),
      '09/20/2026',
    );
    await tester.tap(find.byKey(const Key('delinquency-filter-apply')));
    await tester.pumpAndSettle();

    expect(repository.summaryCalls, 1);
    expect(repository.itemsCalls, 2);
    final call = repository.calls.last;
    expect(call.obligationType, FinancialObligationType.loanInstallment);
    expect(call.status, FinancialObligationStatus.overdue);
    expect(call.overdueOnly, isTrue);
    expect(call.dueFrom, DateTime(2026, 9, 10));
    expect(call.dueTo, DateTime(2026, 9, 20));
  });

  testWidgets('clearing filters reloads the list without query parameters',
      (tester) async {
    final repository = FakeAdminDelinquencyRepository(
      summary: summary(),
      items: [contribution()],
    );

    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();
    await openFilters(tester);
    await selectFilterOption(
      tester,
      const Key('delinquency-filter-type'),
      const Key('delinquency-filter-type-contribution'),
    );
    await tester.tap(find.byKey(const Key('delinquency-filter-apply')));
    await tester.pumpAndSettle();
    await openFilters(tester);
    await tester.tap(find.byKey(const Key('delinquency-filter-clear')));
    await tester.pumpAndSettle();

    expect(repository.summaryCalls, 1);
    expect(repository.itemsCalls, 3);
    final call = repository.calls.last;
    expect(call.obligationType, isNull);
    expect(call.status, isNull);
    expect(call.memberId, isNull);
    expect(call.overdueOnly, isNull);
    expect(call.dueFrom, isNull);
    expect(call.dueTo, isNull);
  });

  testWidgets('pull to refresh keeps filters and reloads global summary',
      (tester) async {
    final repository = FakeAdminDelinquencyRepository(
      summary: summary(),
      items: [contribution()],
    );

    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();
    await openFilters(tester);
    await selectFilterOption(
      tester,
      const Key('delinquency-filter-type'),
      const Key('delinquency-filter-type-contribution'),
    );
    await tester.tap(find.byKey(const Key('delinquency-filter-apply')));
    await tester.pumpAndSettle();

    await tester.fling(find.byType(ListView), const Offset(0, 300), 1000);
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();

    expect(repository.summaryCalls, 2);
    expect(repository.itemsCalls, 3);
    expect(repository.calls.last.obligationType,
        FinancialObligationType.contribution);
  });

  testWidgets('invalid due period shows feedback without reloading list',
      (tester) async {
    final repository = FakeAdminDelinquencyRepository(
      summary: summary(),
      items: [contribution()],
    );

    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();
    await openFilters(tester);
    await selectFilterDate(
      tester,
      const Key('delinquency-filter-due-from'),
      '09/20/2026',
    );
    await selectFilterDate(
      tester,
      const Key('delinquency-filter-due-to'),
      '09/10/2026',
    );
    await tester.tap(find.byKey(const Key('delinquency-filter-apply')));
    await tester.pump();

    expect(find.text('A data inicial não pode ser posterior à data final.'),
        findsOneWidget);
    expect(repository.summaryCalls, 1);
    expect(repository.itemsCalls, 1);
  });
  testWidgets('opens each exact receipt and preserves partial state and summary',
      (tester) async {
    final partial = contribution(
      status: FinancialObligationStatus.partial,
      receiptAvailable: true,
    );
    final repository = FakeAdminDelinquencyRepository(
      summary: summary(),
      items: [partial, installment()],
    );
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();
    expect(repository.receiptPaymentIds, isEmpty);

    for (final entry in {'Ana': 41, 'Bruno': 42}.entries) {
      final card = find.ancestor(
        of: find.text(entry.key),
        matching: find.byType(Card),
      );
      final action = find.descendant(
        of: card,
        matching: find.text('Ver recibo'),
      );
      await tester.scrollUntilVisible(
        action, 200, scrollable: find.byType(Scrollable),
      );
      expect(action, findsOneWidget);
      await tester.tap(action);
      await tester.pumpAndSettle();
      final viewer = tester.widget<PixReceiptScreen>(find.byType(PixReceiptScreen));
      expect(viewer.paymentId, entry.value);
      expect(viewer.repository, same(repository));
      expect(repository.receiptPaymentIds, entry.value == 41 ? [41] : [41, 42]);
      await tester.pump(const Duration(seconds: 10));
      await tester.pageBack();
      await tester.pumpAndSettle();
      expect(repository.summaryCalls, 1);
      expect(repository.itemsCalls, 1);
      expect(find.descendant(
        of: card,
        matching: find.text(entry.value == 41 ? 'Estado: Parcial' : 'Estado: Em atraso'),
      ), findsOneWidget);
      if (entry.value == 41) {
        expect(partial.status, FinancialObligationStatus.partial);
        expect(find.descendant(of: card, matching: find.text('Estado: Quitado')), findsNothing);
        expect(find.descendant(of: card, matching: find.text('Valor pago: R\$ 40.00')), findsOneWidget);
        expect(find.descendant(of: card, matching: find.text('Saldo pendente: R\$ 60.00')), findsOneWidget);
      }
    }
    expect(repository.statusCalls, 0);
    expect(repository.receiptPaymentIds, [41, 42]);
  });

  for (final receiptAvailable in [false, true]) {
    testWidgets('hides receipt action when eligibility is incomplete: $receiptAvailable',
        (tester) async {
      final repository = FakeAdminDelinquencyRepository(
        summary: summary(),
        items: [contribution(
          receiptAvailable: receiptAvailable,
          paymentId: receiptAvailable ? null : 41,
        )],
      );
      await tester.pumpWidget(screen(repository));
      await tester.pumpAndSettle();
      final card = find.ancestor(of: find.text('Ana'), matching: find.byType(Card));
      await tester.scrollUntilVisible(card, 200, scrollable: find.byType(Scrollable));
      expect(find.descendant(of: card, matching: find.text('Ver recibo')), findsNothing);
      expect(repository.receiptPaymentIds, isEmpty);
    });
  }

  testWidgets('receipt round trip preserves all filters without reloading summary',
      (tester) async {
    final repository = FakeAdminDelinquencyRepository(
      summary: summary(),
      items: [contribution(
        status: FinancialObligationStatus.partial,
        receiptAvailable: true,
      )],
    );
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();
    await openFilters(tester);
    await selectFilterOption(tester, const Key('delinquency-filter-type'),
        const Key('delinquency-filter-type-contribution'));
    await selectFilterOption(tester, const Key('delinquency-filter-status'),
        const Key('delinquency-filter-status-partial'));
    await tester.tap(find.byKey(const Key('delinquency-filter-overdue')));
    await tester.pumpAndSettle();
    await selectFilterDate(tester, const Key('delinquency-filter-due-from'), '09/10/2026');
    await selectFilterDate(tester, const Key('delinquency-filter-due-to'), '09/20/2026');
    await tester.tap(find.byKey(const Key('delinquency-filter-apply')));
    await tester.pumpAndSettle();
    expect(repository.itemsCalls, 2);

    final card = find.ancestor(of: find.text('Ana'), matching: find.byType(Card));
    final action = find.descendant(of: card, matching: find.text('Ver recibo'));
    await tester.scrollUntilVisible(action, 200, scrollable: find.byType(Scrollable));
    await tester.tap(action);
    await tester.pumpAndSettle();
    expect(find.byType(PixReceiptScreen), findsOneWidget);
    expect(repository.receiptPaymentIds, [41]);
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(repository.summaryCalls, 1);
    expect(repository.itemsCalls, 2);

    await openFilters(tester);
    expect(tester.widget<SwitchListTile>(
      find.byKey(const Key('delinquency-filter-overdue')),
    ).value, isTrue);
    expect(find.text('Data inicial de vencimento: 10/09/2026'), findsOneWidget);
    expect(find.text('Data final de vencimento: 20/09/2026'), findsOneWidget);
    await tester.tap(find.byKey(const Key('delinquency-filter-apply')));
    await tester.pumpAndSettle();

    expect(repository.itemsCalls, 3);
    expect(repository.summaryCalls, 1);
    for (final call in repository.calls.skip(1)) {
      expect(call.obligationType, FinancialObligationType.contribution);
      expect(call.status, FinancialObligationStatus.partial);
      expect(call.overdueOnly, isTrue);
      expect(call.dueFrom, DateTime(2026, 9, 10));
      expect(call.dueTo, DateTime(2026, 9, 20));
    }

    await openFilters(tester);
    await tester.tap(find.byKey(const Key('delinquency-filter-clear')));
    await tester.pumpAndSettle();
    expect(repository.itemsCalls, 4);
    expect(repository.summaryCalls, 1);
    final cleared = repository.calls[3];
    expect(cleared.obligationType, isNull);
    expect(cleared.status, isNull);
    expect(cleared.overdueOnly, isNull);
    expect(cleared.dueFrom, isNull);
    expect(cleared.dueTo, isNull);
    await tester.scrollUntilVisible(
      find.text('R\$ 500.00'), -200, scrollable: find.byType(Scrollable),
    );
    expect(find.text('R\$ 500.00'), findsOneWidget);
  });

}
