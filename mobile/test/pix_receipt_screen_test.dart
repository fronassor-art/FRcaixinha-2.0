import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:frcaixinha/models/finance_models.dart';
import 'package:frcaixinha/repositories/app_repository.dart';
import 'package:frcaixinha/screens/payments/pix_receipt_screen.dart';
import 'package:frcaixinha/services/api_client.dart';

class FakeReceiptRepository extends AppRepository {
  FakeReceiptRepository(this.responses) : super(ApiClient());
  final List<Object> responses;
  int calls = 0;

  @override
  Future<PixReceipt> paymentReceipt(int paymentId) async {
    final response = responses[calls++];
    if (response is Exception) throw response;
    if (response is Future<PixReceipt>) return response;
    return response as PixReceipt;
  }
}

PixReceipt receipt({bool optionalFields = false}) => PixReceipt.fromJson({
      'receipt_version': 'v1',
      'receipt_number': 'PIX-V1-0001',
      'payment': optionalFields
          ? {}
          : {
              'id': 77,
              'provider': 'mercado_pago',
              'provider_order_id': 'order-77',
              'provider_payment_id': 'provider-77',
              'external_reference': 'contribution-9',
              'pix_txid': 'tx-77',
              'end_to_end_id': 'e2e-77',
              'reference_type': 'CONTRIBUTION',
              'reference_id': '9',
            },
      'obligation': optionalFields
          ? {}
          : {
              'type': 'CONTRIBUTION',
              'contribution_id': 9,
              'member_id': 4,
              'status_before': 'PARTIAL',
              'status_after': 'PAID',
            },
      'amounts': {
        'received': '120.00',
        'applied': '115.00',
        'principal': '80.00',
        'interest': '20.00',
        'penalty': '15.00',
        'excess': '5.00',
      },
      'confirmation': optionalFields
          ? {}
          : {
              'source': 'WEBHOOK',
              'confirmed_at': '2026-09-14T10:30:00+00:00',
              'webhook_event_id': 31,
            },
      'ledger_entries': optionalFields
          ? []
          : [
              {
                'id': 12,
                'account': 'CONTRIBUTIONS',
                'direction': 'CREDIT',
                'amount': '115.00',
                'reference_type': 'CONTRIBUTION_PAYMENT',
                'reference_id': '77',
                'entry_hash': 'abc123',
              },
            ],
    });

Widget screen(FakeReceiptRepository repository) =>
    MaterialApp(home: PixReceiptScreen(paymentId: 77, repository: repository));

void main() {
  testWidgets('shows initial loading without repeated receipt calls', (tester) async {
    final pending = Completer<PixReceipt>();
    final repository = FakeReceiptRepository([pending.future]);
    await tester.pumpWidget(screen(repository));
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    await tester.pump(const Duration(seconds: 10));
    expect(repository.calls, 1);
    pending.complete(receipt());
  });

  testWidgets('shows typed receipt data without recalculating values', (tester) async {
    await tester.pumpWidget(screen(FakeReceiptRepository([receipt()])));
    await tester.pumpAndSettle();

    expect(find.text('Número: PIX-V1-0001'), findsOneWidget);
    expect(find.text('Provedor: mercado_pago'), findsOneWidget);
    expect(find.text('Estado antes: PARTIAL'), findsOneWidget);
    expect(find.text('Estado após: PAID'), findsOneWidget);
    expect(find.text('Recebido: R\$ 120.00'), findsOneWidget);
    expect(find.text('Aplicado: R\$ 115.00'), findsOneWidget);
    expect(find.text('Principal: R\$ 80.00'), findsOneWidget);
    expect(find.text('Juros: R\$ 20.00'), findsOneWidget);
    expect(find.text('Multa: R\$ 15.00'), findsOneWidget);
    expect(find.text('Excesso: R\$ 5.00'), findsOneWidget);
    await tester.scrollUntilVisible(find.text('Lançamentos de ledger'), 200,
        scrollable: find.byType(Scrollable).first);
    expect(find.text('Conta: CONTRIBUTIONS'), findsOneWidget);
    expect(find.text('Valor: R\$ 115.00'), findsOneWidget);
  });

  testWidgets('tolerates optional receipt fields', (tester) async {
    await tester.pumpWidget(screen(FakeReceiptRepository([receipt(optionalFields: true)])));
    await tester.pumpAndSettle();
    expect(find.text('Recibo PIX'), findsOneWidget);
    expect(find.text('Recebido: R\$ 120.00'), findsOneWidget);
    expect(find.text('Provedor:'), findsNothing);
  });

  testWidgets('shows error and retries receipt request', (tester) async {
    final repository = FakeReceiptRepository([Exception('offline'), receipt()]);
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();
    expect(find.text('Tentar novamente'), findsOneWidget);
    await tester.tap(find.text('Tentar novamente'));
    await tester.pumpAndSettle();
    expect(find.text('Número: PIX-V1-0001'), findsOneWidget);
    expect(repository.calls, 2);
  });
}
