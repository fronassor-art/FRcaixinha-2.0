import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:frcaixinha/models/finance_models.dart';
import 'package:frcaixinha/repositories/app_repository.dart';
import 'package:frcaixinha/screens/statement_screen.dart';
import 'package:frcaixinha/screens/payments/pix_receipt_screen.dart';
import 'package:frcaixinha/services/api_client.dart';

class FakeStatementRepository extends AppRepository {
  FakeStatementRepository(this.responses) : super(ApiClient());
  final List<Object> responses;
  int calls = 0;
  final List<int> receiptPaymentIds = [];

  @override
  Future<PixReceipt> paymentReceipt(int paymentId) async {
    receiptPaymentIds.add(paymentId);
    return PixReceipt.fromJson({
      'receipt_version': 'v1',
      'receipt_number': 'PIX-' + paymentId.toString(),
      'payment': {'id': paymentId},
      'obligation': {},
      'amounts': {},
      'confirmation': {},
      'ledger_entries': [],
    });
  }

  @override
  Future<MemberStatement> statement() async {
    final response = responses[calls++];
    if (response is Exception) throw response;
    if (response is Future<MemberStatement>) return response;
    return response as MemberStatement;
  }
}

MemberStatement statement(List<Map<String, dynamic>> movements) => MemberStatement.fromJson({
  'totals': {'contributions_paid': '100.00', 'loan_payments': '50.00', 'loan_outstanding': '200.00'},
  'movements': movements,
});

Map<String, dynamic> movement({
  String id = 'payment:1',
  String type = 'CONTRIBUTION_PAYMENT',
  String direction = 'DEBIT',
  String total = '100.00',
  String description = 'Pagamento de contribuição',
  String? competence,
  int? loanId,
  int? installmentNumber,
  String? principal,
  String? interest,
  String? penalty,
  int? paymentId,
  bool receiptAvailable = false,
}) => {
  'id': id,
  'type': type,
  'direction': direction,
  'occurred_at': '2026-09-14T10:30:00+00:00',
  'description': description,
  'total': total,
  'competence': competence,
  'loan_id': loanId,
  'installment_number': installmentNumber,
  'principal': principal,
  'interest': interest,
  'penalty': penalty,
  'payment_id': paymentId,
  'receipt_available': receiptAvailable,
};

Widget screen(FakeStatementRepository repository) => MaterialApp(home: StatementScreen(repository: repository));

void main() {
  test('parses nullable legacy statement and movement fields', () {
    final value = MemberStatement.fromJson({'totals': {}, 'contributions': []});
    expect(value.movements, isEmpty);
    expect(value.totals.contributionsPaid, '0.00');
  });

  testWidgets('shows initial loading', (tester) async {
    final pending = Completer<MemberStatement>();
    await tester.pumpWidget(screen(FakeStatementRepository([pending.future])));
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    pending.complete(statement([]));
  });

  testWidgets('shows error and retries', (tester) async {
    final repository = FakeStatementRepository([Exception('offline'), statement([])]);
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();
    expect(find.text('Tentar novamente'), findsOneWidget);
    await tester.tap(find.text('Tentar novamente'));
    await tester.pumpAndSettle();
    expect(find.text('Nenhuma movimentação financeira encontrada.'), findsOneWidget);
    expect(repository.calls, 2);
  });

  testWidgets('shows empty statement without pending obligations', (tester) async {
    await tester.pumpWidget(screen(FakeStatementRepository([statement([])])));
    await tester.pumpAndSettle();
    expect(find.text('Nenhuma movimentação financeira encontrada.'), findsOneWidget);
    expect(find.text('Contribuições'), findsNothing);
  });

  testWidgets('shows debit contribution with competence and receipt', (tester) async {
    await tester.pumpWidget(screen(FakeStatementRepository([
      statement([movement(competence: '2026-09-01', receiptAvailable: true)]),
    ])));
    await tester.pumpAndSettle();
    expect(find.text('Saída'), findsOneWidget);
    expect(find.text('Contribuição'), findsOneWidget);
    expect(find.text('Competência: 2026-09-01'), findsOneWidget);
    expect(find.text('Recibo disponível'), findsOneWidget);
  });

  testWidgets('shows installment components and loan disbursement credit', (tester) async {
    await tester.pumpWidget(screen(FakeStatementRepository([
      statement([
        movement(id: 'payment:2', type: 'LOAN_INSTALLMENT_PAYMENT', total: '130.00', loanId: 7, installmentNumber: 2, principal: '100.00', interest: '20.00', penalty: '10.00'),
        movement(id: 'ledger:3', type: 'LOAN_DISBURSEMENT', direction: 'CREDIT', total: '500.00', description: 'Liberação do empréstimo #7', loanId: 7),
      ]),
    ])));
    await tester.pumpAndSettle();
    expect(find.text('Pagamento de parcela'), findsOneWidget);
    expect(find.text('Principal: R\$ 100.00'), findsOneWidget);
    expect(find.text('Juros: R\$ 20.00'), findsOneWidget);
    expect(find.text('Multa: R\$ 10.00'), findsOneWidget);
    await tester.scrollUntilVisible(find.text('Liberação de empréstimo'), 200, scrollable: find.byType(Scrollable).first);
    expect(find.text('Entrada'), findsOneWidget);
    expect(find.text('Liberação de empréstimo'), findsOneWidget);
  });

  testWidgets('pull to refresh reloads movements', (tester) async {
    final repository = FakeStatementRepository([
      statement([movement(total: '10.00')]),
      statement([movement(id: 'payment:2', total: '20.00')]),
    ]);
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();
    await tester.fling(find.byType(Scrollable), const Offset(0, 300), 1000);
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();
    expect(repository.calls, 2);
    expect(find.text('R\$ 20.00'), findsOneWidget);
  });

  testWidgets('opens receipt with the exact movement payment id', (tester) async {
    final repository = FakeStatementRepository([
      statement([movement(id: 'payment:41', paymentId: 41, receiptAvailable: true)]),
    ]);
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();

    expect(find.text('Ver recibo'), findsOneWidget);
    await tester.tap(find.text('Ver recibo'));
    await tester.pumpAndSettle();
    final receiptScreen = tester.widget<PixReceiptScreen>(find.byType(PixReceiptScreen));
    expect(receiptScreen.paymentId, 41);
    expect(repository.receiptPaymentIds, [41]);
  });

  testWidgets('does not show receipt action without receipt or payment id', (tester) async {
    await tester.pumpWidget(screen(FakeStatementRepository([
      statement([
        movement(id: 'payment:10', paymentId: 10),
        movement(id: 'payment:11', receiptAvailable: true),
      ]),
    ])));
    await tester.pumpAndSettle();
    expect(find.text('Ver recibo'), findsNothing);
  });

  testWidgets('partial payment movements open their own receipts', (tester) async {
    final repository = FakeStatementRepository([
      statement([
        movement(id: 'payment:51', total: '40.00', paymentId: 51, receiptAvailable: true),
        movement(id: 'payment:52', total: '60.00', paymentId: 52, receiptAvailable: true),
      ]),
    ]);
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();

    final firstMovement = find.ancestor(
      of: find.text('R\$ 40.00'),
      matching: find.byType(Card),
    );
    await tester.scrollUntilVisible(firstMovement, 200,
        scrollable: find.byType(Scrollable).first);
    await tester.tap(find.descendant(
      of: firstMovement,
      matching: find.text('Ver recibo'),
    ));
    await tester.pumpAndSettle();
    expect(repository.receiptPaymentIds, [51]);
    await tester.pageBack();
    await tester.pumpAndSettle();
    final secondMovement = find.ancestor(
      of: find.text('R\$ 60.00'),
      matching: find.byType(Card),
    );
    await tester.scrollUntilVisible(secondMovement, 200,
        scrollable: find.byType(Scrollable).first);
    await tester.tap(find.descendant(
      of: secondMovement,
      matching: find.text('Ver recibo'),
    ));
    await tester.pumpAndSettle();
    final receiptScreen = tester.widget<PixReceiptScreen>(find.byType(PixReceiptScreen));
    expect(receiptScreen.paymentId, 52);
    expect(repository.receiptPaymentIds, [51, 52]);
  });
}
