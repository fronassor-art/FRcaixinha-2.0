import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:frcaixinha/models/finance_models.dart';
import 'package:frcaixinha/repositories/app_repository.dart';
import 'package:frcaixinha/screens/loans/loan_detail_screen.dart';
import 'package:frcaixinha/screens/payments/pix_payment_screen.dart';
import 'package:frcaixinha/services/api_client.dart';

class FakeLoanDetailRepository extends AppRepository {
  FakeLoanDetailRepository({required this.loanData, required this.obligations, this.status}) : super(ApiClient());
  final Map<String, dynamic> loanData;
  final List<FinancialObligation> obligations;
  final PixPaymentStatusDetails? status;
  int loanCalls = 0;
  int obligationsCalls = 0;
  final List<int> createdPixFor = [];

  @override
  Future<Map<String, dynamic>> loan(int id) async {
    loanCalls++;
    return loanData;
  }

  @override
  Future<List<FinancialObligation>> financialObligations() async {
    obligationsCalls++;
    return obligations;
  }

  @override
  Future<PixPayment> createLoanInstallmentPix(int installmentId) async {
    createdPixFor.add(installmentId);
    return PixPayment(paymentId: 81, providerPaymentId: 'provider-81', status: 'pending', amount: '120.00', qrCode: 'pix-code');
  }

  @override
  Future<PixPaymentStatusDetails> paymentStatus(int paymentId) async => status ??
      PixPaymentStatusDetails.fromJson({'payment_id': paymentId, 'provider_payment_id': 'provider-81', 'status': 'cancelled', 'amount': '120.00', 'obligation_status': 'PENDING'});
}

Map<String, dynamic> loan({int installmentId = 11, String installmentStatus = 'OPEN'}) => {
      'id': 7,
      'status': 'ACTIVE',
      'principal': '100.00',
      'monthly_rate': '0.20',
      'installments': [
        {
          'id': installmentId,
          'number': 1,
          'due_date': '2026-09-10',
          'principal': '100.00',
          'interest': '20.00',
          'amount': '120.00',
          'penalty_amount': '5.00',
          'paid_amount': '0.00',
          'remaining': '125.00',
          'status': installmentStatus,
        }
      ],
    };

FinancialObligation installmentObligation({
  int id = 11,
  FinancialObligationStatus status = FinancialObligationStatus.pending,
  String paid = '0.00',
  String outstanding = '125.00',
  String principal = '100.00',
  String interest = '20.00',
  String penalty = '5.00',
  int daysOverdue = 0,
  bool receiptAvailable = false,
  FinancialObligationType type = FinancialObligationType.loanInstallment,
}) => FinancialObligation(
      memberId: 1,
      type: type,
      obligationId: id,
      competence: null,
      loanId: 7,
      installmentNumber: 1,
      dueDate: DateTime.parse('2026-09-10'),
      amountDue: '120.00',
      amountPaid: paid,
      outstandingAmount: outstanding,
      financialStatus: status,
      daysOverdue: daysOverdue,
      principalOutstanding: principal,
      interestOutstanding: interest,
      penaltyOutstanding: penalty,
      paymentId: 81,
      receiptAvailable: receiptAvailable,
    );

Widget screen(FakeLoanDetailRepository repository) =>
    MaterialApp(home: LoanDetailScreen(loanId: 7, repository: repository));

void main() {
  testWidgets('shows pending installment financial fields from correlated obligation', (tester) async {
    final repository = FakeLoanDetailRepository(loanData: loan(), obligations: [installmentObligation()]);
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();

    expect(find.text('Parcela 1 • R\$ 120.00'), findsOneWidget);
    expect(find.textContaining('Valor pago: R\$ 0.00'), findsOneWidget);
    expect(find.textContaining('Saldo pendente: R\$ 125.00'), findsOneWidget);
    expect(
      find.descendant(
        of: find.ancestor(of: find.text('Parcela 1 • R\$ 120.00'), matching: find.byType(Card)),
        matching: find.textContaining('Principal: R\$ 100.00'),
      ),
      findsOneWidget,
    );
    expect(find.textContaining('Juros: R\$ 20.00'), findsOneWidget);
    expect(find.textContaining('Multa: R\$ 5.00'), findsOneWidget);
    expect(find.textContaining('Vencimento: 2026-09-10'), findsOneWidget);
    expect(find.textContaining('Estado: Pendente'), findsOneWidget);
  });

  testWidgets('shows partial official amounts without marking installment paid', (tester) async {
    final repository = FakeLoanDetailRepository(
      loanData: loan(installmentStatus: 'PAID'),
      obligations: [installmentObligation(status: FinancialObligationStatus.partial, paid: '40.00', outstanding: '85.00')],
    );
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();

    expect(find.textContaining('Valor pago: R\$ 40.00'), findsOneWidget);
    expect(find.textContaining('Saldo pendente: R\$ 85.00'), findsOneWidget);
    expect(find.textContaining('Estado: Parcial'), findsOneWidget);
    expect(find.textContaining('Quitado'), findsNothing);
    expect(find.text('Pagar Pix'), findsOneWidget);
  });

  testWidgets('shows overdue installment with official interest and penalty', (tester) async {
    final repository = FakeLoanDetailRepository(
      loanData: loan(),
      obligations: [installmentObligation(status: FinancialObligationStatus.overdue, paid: '25.00', outstanding: '100.00', interest: '18.00', penalty: '7.00', daysOverdue: 9)],
    );
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();

    expect(find.textContaining('Estado: Em atraso'), findsOneWidget);
    expect(find.textContaining('9 dias em atraso'), findsOneWidget);
    expect(find.textContaining('Saldo pendente: R\$ 100.00'), findsOneWidget);
    expect(find.textContaining('Juros: R\$ 18.00'), findsOneWidget);
    expect(find.textContaining('Multa: R\$ 7.00'), findsOneWidget);
  });

  testWidgets('shows paid installment without payment action', (tester) async {
    final repository = FakeLoanDetailRepository(
      loanData: loan(),
      obligations: [installmentObligation(status: FinancialObligationStatus.paid, paid: '120.00', outstanding: '0.00', principal: '0.00', interest: '0.00', penalty: '0.00')],
    );
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();

    expect(find.textContaining('Estado: Quitado'), findsOneWidget);
    expect(find.byIcon(Icons.check_circle), findsOneWidget);
    expect(find.text('Pagar Pix'), findsNothing);
  });

  testWidgets('falls back to loan detail when no installment obligation is correlated', (tester) async {
    final repository = FakeLoanDetailRepository(
      loanData: loan(),
      obligations: [installmentObligation(id: 99, type: FinancialObligationType.contribution)],
    );
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();

    expect(find.textContaining('Saldo pendente: R\$ 125.00'), findsOneWidget);
    expect(
      find.descendant(
        of: find.ancestor(of: find.text('Parcela 1 • R\$ 120.00'), matching: find.byType(Card)),
        matching: find.textContaining('Principal: R\$ 100.00'),
      ),
      findsOneWidget,
    );
    expect(find.textContaining('Juros: R\$ 20.00'), findsOneWidget);
    expect(find.textContaining('Multa: R\$ 5.00'), findsOneWidget);
  });

  testWidgets('creates Pix, opens reusable screen, and passes only correlated receipt availability', (tester) async {
    final repository = FakeLoanDetailRepository(
      loanData: loan(),
      obligations: [
        installmentObligation(id: 99, receiptAvailable: false),
        installmentObligation(receiptAvailable: true),
      ],
    );
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Pagar Pix'));
    await tester.pumpAndSettle();

    expect(repository.createdPixFor, [11]);
    final pixScreen = tester.widget<PixPaymentScreen>(find.byType(PixPaymentScreen));
    expect(pixScreen.receiptAvailable, isTrue);
  });

  testWidgets('approved Pix with partial obligation reloads without marking installment paid', (tester) async {
    final repository = FakeLoanDetailRepository(
      loanData: loan(),
      obligations: [installmentObligation(status: FinancialObligationStatus.partial, paid: '40.00', outstanding: '85.00')],
      status: PixPaymentStatusDetails.fromJson({'payment_id': 81, 'provider_payment_id': 'provider-81', 'status': 'approved', 'amount': '40.00', 'obligation_status': 'PARTIAL'}),
    );
    await tester.pumpWidget(screen(repository));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Pagar Pix'));
    await tester.pumpAndSettle();

    expect(find.text('Pagamento confirmado. A obrigação pode permanecer parcial.'), findsOneWidget);
    expect(repository.loanCalls, greaterThanOrEqualTo(2));
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(find.textContaining('Estado: Parcial'), findsOneWidget);
    expect(find.textContaining('Quitado'), findsNothing);
    expect(repository.loanCalls, greaterThanOrEqualTo(3));
    expect(repository.obligationsCalls, greaterThanOrEqualTo(3));
  });
}
