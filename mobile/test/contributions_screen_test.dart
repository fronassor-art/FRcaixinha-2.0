import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:frcaixinha/models/finance_models.dart';
import 'package:frcaixinha/repositories/app_repository.dart';
import 'package:frcaixinha/screens/contributions_screen.dart';
import 'package:frcaixinha/screens/payments/pix_payment_screen.dart';
import 'package:frcaixinha/services/api_client.dart';

class FakeContributionsRepository extends AppRepository {
  FakeContributionsRepository({required this.items, required this.obligations, this.status}) : super(ApiClient());

  final List<Map<String, dynamic>> items;
  final List<FinancialObligation> obligations;
  final PixPaymentStatusDetails? status;
  int contributionsCalls = 0;
  int summaryCalls = 0;
  int obligationsCalls = 0;
  final List<int> createdPixFor = [];

  @override
  Future<Map<String, dynamic>> contributions() async {
    contributionsCalls++;
    return {'items': items};
  }

  @override
  Future<Map<String, dynamic>> contributionSummary() async {
    summaryCalls++;
    return {'paid_total': '0.00', 'pending_total': '100.00', 'expected_total': '100.00'};
  }

  @override
  Future<List<FinancialObligation>> financialObligations() async {
    obligationsCalls++;
    return obligations;
  }

  @override
  Future<PixPayment> createContributionPix(int contributionId) async {
    createdPixFor.add(contributionId);
    return PixPayment(paymentId: 55, providerPaymentId: 'provider-55', status: 'pending', amount: '100.00', qrCode: 'pix-code');
  }

  @override
  Future<PixPaymentStatusDetails> paymentStatus(int paymentId) async =>
      status ?? PixPaymentStatusDetails.fromJson({'payment_id': paymentId, 'provider_payment_id': 'provider-55', 'status': 'pending', 'amount': '100.00', 'obligation_status': 'PENDING'});
}

Map<String, dynamic> contribution({int id = 1, String status = 'PENDING', String paidAmount = '0.00'}) => {
      'id': id,
      'competence': '2026-09-01',
      'amount': '100.00',
      'paid_amount': paidAmount,
      'due_date': '2026-09-10',
      'status': status,
    };

FinancialObligation obligation({
  int id = 1,
  FinancialObligationStatus status = FinancialObligationStatus.pending,
  String paid = '0.00',
  String outstanding = '100.00',
  int daysOverdue = 0,
  bool receiptAvailable = false,
  FinancialObligationType type = FinancialObligationType.contribution,
}) => FinancialObligation(
      memberId: 1,
      type: type,
      obligationId: id,
      competence: '2026-09-01',
      loanId: null,
      installmentNumber: null,
      dueDate: DateTime.parse('2026-09-10'),
      amountDue: '100.00',
      amountPaid: paid,
      outstandingAmount: outstanding,
      financialStatus: status,
      daysOverdue: daysOverdue,
      principalOutstanding: '0.00',
      interestOutstanding: '0.00',
      penaltyOutstanding: '0.00',
      paymentId: 55,
      receiptAvailable: receiptAvailable,
    );

Widget screen(FakeContributionsRepository repository) =>
    MaterialApp(home: ContributionsScreen(repository: repository));

void main() {
  testWidgets('shows pending contribution financial fields', (tester) async {
    final repository = FakeContributionsRepository(items: [contribution()], obligations: [obligation()]);
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();

    expect(find.text('Competência 2026-09'), findsOneWidget);
    expect(find.textContaining('Valor original: R\$ 100.00'), findsOneWidget);
    expect(find.textContaining('Valor pago: R\$ 0.00'), findsOneWidget);
    expect(find.textContaining('Saldo pendente: R\$ 100.00'), findsOneWidget);
    expect(find.textContaining('Vencimento: 2026-09-10'), findsOneWidget);
    expect(find.textContaining('Estado: Pendente'), findsOneWidget);
  });

  testWidgets('shows partial contribution without marking it paid', (tester) async {
    final repository = FakeContributionsRepository(
      items: [contribution(status: 'PAID', paidAmount: '10.00')],
      obligations: [obligation(status: FinancialObligationStatus.partial, paid: '40.00', outstanding: '60.00')],
    );
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();

    expect(find.textContaining('Valor pago: R\$ 40.00'), findsOneWidget);
    expect(find.textContaining('Saldo pendente: R\$ 60.00'), findsOneWidget);
    expect(find.textContaining('Estado: Parcial'), findsOneWidget);
    expect(find.text('Pagar Pix'), findsOneWidget);
    expect(find.textContaining('Quitado'), findsNothing);
  });

  testWidgets('shows overdue contribution with days and outstanding balance', (tester) async {
    final repository = FakeContributionsRepository(
      items: [contribution()],
      obligations: [obligation(status: FinancialObligationStatus.overdue, paid: '25.00', outstanding: '75.00', daysOverdue: 9)],
    );
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();

    expect(find.textContaining('Estado: Em atraso'), findsOneWidget);
    expect(find.textContaining('9 dias em atraso'), findsOneWidget);
    expect(find.textContaining('Saldo pendente: R\$ 75.00'), findsOneWidget);
  });

  testWidgets('shows paid contribution without a payment action', (tester) async {
    final repository = FakeContributionsRepository(
      items: [contribution(status: 'PENDING')],
      obligations: [obligation(status: FinancialObligationStatus.paid, paid: '100.00', outstanding: '0.00')],
    );
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();

    expect(find.textContaining('Estado: Quitado'), findsOneWidget);
    expect(find.byIcon(Icons.check_circle), findsOneWidget);
    expect(find.text('Pagar Pix'), findsNothing);
  });

  testWidgets('creates Pix, opens reusable screen, and passes correlated receipt availability', (tester) async {
    final repository = FakeContributionsRepository(
      items: [contribution()],
      obligations: [
        obligation(id: 99, receiptAvailable: false, type: FinancialObligationType.loanInstallment),
        obligation(receiptAvailable: true),
      ],
      status: PixPaymentStatusDetails.fromJson({'payment_id': 55, 'provider_payment_id': 'provider-55', 'status': 'cancelled', 'amount': '100.00', 'obligation_status': 'PENDING'}),
    );
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Pagar Pix'));
    await tester.pumpAndSettle();

    expect(repository.createdPixFor, [1]);
    final pixScreen = tester.widget<PixPaymentScreen>(find.byType(PixPaymentScreen));
    expect(pixScreen.receiptAvailable, isTrue);
  });

  testWidgets('approved Pix with partial obligation reloads without marking contribution paid', (tester) async {
    final repository = FakeContributionsRepository(
      items: [contribution()],
      obligations: [obligation(status: FinancialObligationStatus.partial, paid: '40.00', outstanding: '60.00')],
      status: PixPaymentStatusDetails.fromJson({'payment_id': 55, 'provider_payment_id': 'provider-55', 'status': 'approved', 'amount': '40.00', 'obligation_status': 'PARTIAL'}),
    );
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Pagar Pix'));
    await tester.pumpAndSettle();

    expect(find.text('Pagamento confirmado. A obrigação pode permanecer parcial.'), findsOneWidget);
    expect(repository.contributionsCalls, greaterThanOrEqualTo(2));
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(find.textContaining('Estado: Parcial'), findsOneWidget);
    expect(find.textContaining('Quitado'), findsNothing);
    expect(repository.contributionsCalls, greaterThanOrEqualTo(3));
    expect(repository.summaryCalls, greaterThanOrEqualTo(3));
    expect(repository.obligationsCalls, greaterThanOrEqualTo(3));
  });
}
