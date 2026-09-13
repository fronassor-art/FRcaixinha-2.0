import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:frcaixinha/models/finance_models.dart';
import 'package:frcaixinha/repositories/app_repository.dart';
import 'package:frcaixinha/screens/loans/loan_request_screen.dart';
import 'package:frcaixinha/services/api_client.dart';

class FakeLoanRepository extends AppRepository {
  FakeLoanRepository(this.result) : super(ApiClient());

  final LoanSimulation result;
  int simulateCalls = 0;
  int confirmCalls = 0;
  String? requestedToken;

  @override
  Future<LoanSimulation> simulateLoan({
    required String principal,
    required int installments,
  }) async {
    simulateCalls++;
    return result;
  }

  @override
  Future<void> confirmLoanSimulation(String simulationToken) async {
    confirmCalls++;
  }

  @override
  Future<Map<String, dynamic>> requestLoan({
    required String principal,
    required int installments,
    required String simulationToken,
  }) async {
    requestedToken = simulationToken;
    return {'id': 1};
  }
}

LoanSimulation sampleSimulation() {
  return LoanSimulation(
    simulationToken: 'token-123',
    calculationVersion: 'linear_amortization_v1',
    principal: '100.00',
    monthlyRate: '0.20',
    installments: 1,
    schedule: [
      LoanSimulationInstallment(
        number: 1,
        principal: '100.00',
        interest: '20.00',
        amount: '120.00',
        balanceBefore: '100.00',
        balanceAfter: '0.00',
      ),
    ],
    totals: LoanSimulationTotals(
      principal: '100.00',
      interest: '20.00',
      payment: '120.00',
      finalBalance: '0.00',
    ),
  );
}

Future<void> pumpLoanRequest(
  WidgetTester tester,
  FakeLoanRepository repository,
) {
  return tester.pumpWidget(
    MaterialApp(home: LoanRequestScreen(repository: repository)),
  );
}

void main() {
  testWidgets('uses fixed rate, one installment by default and disabled request', (
    tester,
  ) async {
    await pumpLoanRequest(tester, FakeLoanRepository(sampleSimulation()));

    expect(find.text('Taxa mensal fixa: 20%'), findsOneWidget);
    expect(find.text('Taxa mensal (ex.: 0.20)'), findsNothing);
    expect(find.text('1'), findsOneWidget);
    expect(
      tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, 'Solicitar empréstimo'),
      ).onPressed,
      isNull,
    );
  });

  testWidgets('requires confirmation and invalidates it after input changes', (
    tester,
  ) async {
    final repository = FakeLoanRepository(sampleSimulation());
    await pumpLoanRequest(tester, repository);

    await tester.enterText(find.byType(TextField), '100');
    await tester.tap(find.text('Simular condições'));
    await tester.pumpAndSettle();

    expect(repository.simulateCalls, 1);
    expect(find.text('Simulação'), findsOneWidget);
    await tester.scrollUntilVisible(find.text('Confirmar simulação'), 200);
    expect(find.text('Confirmar simulação'), findsOneWidget);
    expect(
      tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, 'Solicitar empréstimo'),
      ).onPressed,
      isNull,
    );

    await tester.tap(find.text('Confirmar simulação'));
    await tester.pumpAndSettle();

    expect(repository.confirmCalls, 1);
    expect(find.text('Simulação confirmada'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.widgetWithText(FilledButton, 'Solicitar empréstimo'),
      200,
    );
    expect(
      tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, 'Solicitar empréstimo'),
      ).onPressed,
      isNotNull,
    );

    await tester.scrollUntilVisible(find.byType(TextField), -200);
    await tester.enterText(find.byType(TextField), '200');
    await tester.pump();

    expect(find.text('Simulação'), findsNothing);
    expect(find.text('Simulação confirmada'), findsNothing);
    expect(
      tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, 'Solicitar empréstimo'),
      ).onPressed,
      isNull,
    );
  });
}
