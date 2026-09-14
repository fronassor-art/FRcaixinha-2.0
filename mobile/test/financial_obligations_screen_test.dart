import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:frcaixinha/models/finance_models.dart';
import 'package:frcaixinha/repositories/app_repository.dart';
import 'package:frcaixinha/screens/financial_obligations_screen.dart';
import 'package:frcaixinha/screens/payments/pix_receipt_screen.dart';
import 'package:frcaixinha/services/api_client.dart';

class FakeObligationsRepository extends AppRepository {
  FakeObligationsRepository(this.responses) : super(ApiClient());
  final List<Object> responses; int calls = 0;
  final List<int> receiptPaymentIds = [];
  int statusCalls = 0;
  @override Future<PixReceipt> paymentReceipt(int paymentId) async {
    receiptPaymentIds.add(paymentId);
    return PixReceipt.fromJson({
      'receipt_number': 'PIX-$paymentId',
      'payment': {'id': paymentId},
    });
  }
  @override Future<PixPaymentStatusDetails> paymentStatus(int paymentId) async {
    statusCalls++;
    throw StateError('Receipt viewing must not poll payment status');
  }
  @override Future<List<FinancialObligation>> financialObligations() async { final response = responses[calls++]; if (response is Exception) throw response; if (response is Future<List<FinancialObligation>>) return response; return response as List<FinancialObligation>; }
}
FinancialObligation obligation(Map<String, dynamic> json) => FinancialObligation.fromJson(json);
Widget screen(FakeObligationsRepository repository) => MaterialApp(home: FinancialObligationsScreen(repository: repository));

void main() {
  test('parses contribution partial with optional fields', () {
    final item = obligation({'member_id': 7, 'obligation_type': 'CONTRIBUTION', 'obligation_id': 3, 'competence': '2026-09-01', 'amount_due': '100.00', 'amount_paid': '20.00', 'outstanding_amount': '80.00', 'financial_status': 'PARTIAL'});
    expect(item.type, FinancialObligationType.contribution); expect(item.financialStatus, FinancialObligationStatus.partial); expect(item.loanId, isNull); expect(item.amountPaid, '20.00');
  });
  testWidgets('shows pending contribution and overdue installment components', (tester) async {
    final repository = FakeObligationsRepository([[obligation({'member_id': 1, 'obligation_type': 'CONTRIBUTION', 'obligation_id': 1, 'competence': '2026-09-01', 'due_date': '2026-09-10', 'amount_due': '100.00', 'amount_paid': '0.00', 'outstanding_amount': '100.00', 'financial_status': 'PENDING'}), obligation({'member_id': 1, 'obligation_type': 'LOAN_INSTALLMENT', 'obligation_id': 8, 'loan_id': 4, 'installment_number': 2, 'due_date': '2026-08-01', 'amount_due': '120.00', 'amount_paid': '30.00', 'outstanding_amount': '90.00', 'interest_outstanding': '20.00', 'penalty_outstanding': '20.00', 'days_overdue': 5, 'financial_status': 'OVERDUE'})]]);
    await tester.pumpWidget(screen(repository)); await tester.pumpAndSettle();
    expect(find.text('Estado: Pendente'), findsOneWidget); expect(find.text('Estado: Em atraso'), findsOneWidget); expect(find.text('5 dias em atraso'), findsOneWidget); expect(find.text('Juros pendentes: R\$ 20.00'), findsOneWidget); expect(find.text('Multa pendente: R\$ 20.00'), findsOneWidget);
  });
  testWidgets('shows paid obligation receipt and empty state', (tester) async {
    final repository = FakeObligationsRepository([[obligation({'member_id': 1, 'obligation_type': 'CONTRIBUTION', 'obligation_id': 9, 'amount_due': '100.00', 'amount_paid': '100.00', 'outstanding_amount': '0.00', 'financial_status': 'PAID', 'receipt_available': true})], <FinancialObligation>[]]);
    await tester.pumpWidget(screen(repository)); await tester.pumpAndSettle(); expect(find.text('Estado: Quitado'), findsOneWidget); expect(find.text('Recibo disponível'), findsOneWidget);
  });
  testWidgets('shows empty state', (tester) async {
    await tester.pumpWidget(screen(FakeObligationsRepository([<FinancialObligation>[]])));
    await tester.pumpAndSettle(); expect(find.text('Nenhuma obrigação financeira encontrada.'), findsOneWidget);
  });
  testWidgets('shows error and retries', (tester) async {
    final repository = FakeObligationsRepository([Exception('offline'), <FinancialObligation>[]]);
    await tester.pumpWidget(screen(repository)); await tester.pumpAndSettle(); expect(find.text('Tentar novamente'), findsOneWidget); await tester.tap(find.text('Tentar novamente')); await tester.pumpAndSettle(); expect(find.text('Nenhuma obrigação financeira encontrada.'), findsOneWidget); expect(repository.calls, 2);
  });
  testWidgets('shows loading until obligations arrive', (tester) async {
    final pending = Completer<List<FinancialObligation>>();
    await tester.pumpWidget(screen(FakeObligationsRepository([pending.future])));
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    pending.complete([]);
    await tester.pumpAndSettle();
    expect(find.text('Nenhuma obrigação financeira encontrada.'), findsOneWidget);
  });

  testWidgets('refresh reloads obligations', (tester) async {
    final repository = FakeObligationsRepository([
      [obligation({'financial_status': 'PENDING'})],
      <FinancialObligation>[],
    ]);
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();
    await tester.fling(find.byType(Scrollable), const Offset(0, 300), 1000);
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();
    expect(repository.calls, 2);
    expect(find.text('Nenhuma obrigação financeira encontrada.'), findsOneWidget);
  });

  testWidgets('paid and partial obligations open their exact receipts without changing status', (tester) async {
    final items = [
      obligation({
        'obligation_type': 'CONTRIBUTION', 'obligation_id': 9,
        'competence': '2026-08-01', 'financial_status': 'PAID',
        'amount_due': '100.00', 'amount_paid': '100.00',
        'outstanding_amount': '0.00', 'receipt_available': true, 'payment_id': 41,
      }),
      obligation({
        'obligation_type': 'CONTRIBUTION', 'obligation_id': 10,
        'competence': '2026-09-01', 'financial_status': 'PARTIAL',
        'amount_due': '100.00', 'amount_paid': '20.00',
        'outstanding_amount': '80.00', 'receipt_available': true, 'payment_id': 52,
      }),
    ];
    final repository = FakeObligationsRepository([items]);
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();
    expect(repository.receiptPaymentIds, isEmpty);

    for (final item in items) {
      final card = find.ancestor(
        of: find.text('Competência: ${item.competence}'),
        matching: find.byType(Card),
      );
      final action = find.descendant(of: card, matching: find.text('Ver recibo'));
      await tester.scrollUntilVisible(action, 200, scrollable: find.byType(Scrollable));
      expect(action, findsOneWidget);
      await tester.tap(action);
      await tester.pumpAndSettle();
      final viewer = tester.widget<PixReceiptScreen>(find.byType(PixReceiptScreen));
      expect(viewer.paymentId, item.paymentId);
      expect(viewer.repository, same(repository));
      expect(repository.receiptPaymentIds, item.paymentId == 41 ? [41] : [41, 52]);
      await tester.pump(const Duration(seconds: 10));
      await tester.pageBack();
      await tester.pumpAndSettle();
      expect(find.descendant(
        of: card,
        matching: find.text(item.paymentId == 41 ? 'Estado: Quitado' : 'Estado: Parcial'),
      ), findsOneWidget);
      if (item.paymentId == 52) {
        expect(item.financialStatus, FinancialObligationStatus.partial);
        expect(find.descendant(of: card, matching: find.text('Estado: Quitado')), findsNothing);
        expect(find.descendant(of: card, matching: find.text('Valor pago: R\$ 20.00')), findsOneWidget);
        expect(find.descendant(of: card, matching: find.text('Saldo: R\$ 80.00')), findsOneWidget);
      }
    }
    expect(repository.calls, 1);
    expect(repository.statusCalls, 0);
    expect(repository.receiptPaymentIds, [41, 52]);
  });

  for (final receiptAvailable in [false, true]) {
    testWidgets('hides receipt action with receiptAvailable=$receiptAvailable and missing eligibility', (tester) async {
      final repository = FakeObligationsRepository([
        [obligation({
          'financial_status': 'PAID',
          'receipt_available': receiptAvailable,
          'payment_id': receiptAvailable ? null : 73,
        })],
      ]);
      await tester.pumpWidget(screen(repository));
      await tester.pumpAndSettle();
      expect(find.text('Ver recibo'), findsNothing);
      expect(repository.receiptPaymentIds, isEmpty);
    });
  }

}
