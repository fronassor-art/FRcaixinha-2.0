class ContributionItem {
  final int id; final String competence; final String amount; final String status; final Map<String,dynamic>? payment;
  ContributionItem({required this.id, required this.competence, required this.amount, required this.status, this.payment});
  factory ContributionItem.fromJson(Map<String,dynamic> j) => ContributionItem(
    id: j['id'], competence: j['competence'], amount: j['amount'], status: j['status'],
    payment: j['payment'] == null ? null : Map<String,dynamic>.from(j['payment']),
  );
}
class PixPayment {
  final int paymentId; final String providerPaymentId; final String status; final String amount;
  final String? qrCode; final String? qrCodeBase64; final String? ticketUrl;
  PixPayment({required this.paymentId, required this.providerPaymentId, required this.status, required this.amount, this.qrCode, this.qrCodeBase64, this.ticketUrl});
  factory PixPayment.fromJson(Map<String,dynamic> j) => PixPayment(
    paymentId: j['payment_id'], providerPaymentId: '${j['provider_payment_id']}', status: '${j['status']}', amount: '${j['amount']}',
    qrCode: j['qr_code'], qrCodeBase64: j['qr_code_base64'], ticketUrl: j['ticket_url'],
  );
}

enum FinancialObligationType { contribution, loanInstallment }
enum FinancialObligationStatus { pending, partial, overdue, paid }
FinancialObligationType financialObligationTypeFromJson(Object? value) => value?.toString().toUpperCase() == 'LOAN_INSTALLMENT' ? FinancialObligationType.loanInstallment : FinancialObligationType.contribution;
FinancialObligationStatus financialObligationStatusFromJson(Object? value) {
  switch (value?.toString().toUpperCase()) {
    case 'PARTIAL': return FinancialObligationStatus.partial;
    case 'OVERDUE': return FinancialObligationStatus.overdue;
    case 'PAID': return FinancialObligationStatus.paid;
    default: return FinancialObligationStatus.pending;
  }
}
class FinancialObligation {
  final int memberId, obligationId, daysOverdue;
  final FinancialObligationType type;
  final String? competence;
  final int? loanId, installmentNumber, paymentId;
  final DateTime? dueDate;
  final String amountDue, amountPaid, outstandingAmount, principalOutstanding, interestOutstanding, penaltyOutstanding;
  final FinancialObligationStatus financialStatus;
  final bool receiptAvailable;
  const FinancialObligation({required this.memberId, required this.type, required this.obligationId, required this.competence, required this.loanId, required this.installmentNumber, required this.dueDate, required this.amountDue, required this.amountPaid, required this.outstandingAmount, required this.financialStatus, required this.daysOverdue, required this.principalOutstanding, required this.interestOutstanding, required this.penaltyOutstanding, required this.paymentId, required this.receiptAvailable});
  factory FinancialObligation.fromJson(Map<String, dynamic> json) {
    int? integer(Object? value) => value is int ? value : int.tryParse('$value');
    String money(Object? value) => value?.toString() ?? '0.00';
    return FinancialObligation(memberId: integer(json['member_id']) ?? 0, type: financialObligationTypeFromJson(json['obligation_type']), obligationId: integer(json['obligation_id']) ?? 0, competence: json['competence']?.toString(), loanId: integer(json['loan_id']), installmentNumber: integer(json['installment_number']), dueDate: json['due_date'] == null ? null : DateTime.tryParse('${json['due_date']}'), amountDue: money(json['amount_due']), amountPaid: money(json['amount_paid']), outstandingAmount: money(json['outstanding_amount']), financialStatus: financialObligationStatusFromJson(json['financial_status']), daysOverdue: integer(json['days_overdue']) ?? 0, principalOutstanding: money(json['principal_outstanding']), interestOutstanding: money(json['interest_outstanding']), penaltyOutstanding: money(json['penalty_outstanding']), paymentId: integer(json['payment_id']), receiptAvailable: json['receipt_available'] == true);
  }
}

class LoanSimulationInstallment {
  final int number;
  final String principal;
  final String interest;
  final String amount;
  final String balanceBefore;
  final String balanceAfter;

  LoanSimulationInstallment({
    required this.number,
    required this.principal,
    required this.interest,
    required this.amount,
    required this.balanceBefore,
    required this.balanceAfter,
  });

  factory LoanSimulationInstallment.fromJson(Map<String, dynamic> json) {
    return LoanSimulationInstallment(
      number: json['number'] as int,
      principal: json['principal'].toString(),
      interest: json['interest'].toString(),
      amount: json['amount'].toString(),
      balanceBefore: json['balance_before'].toString(),
      balanceAfter: json['balance_after'].toString(),
    );
  }
}

class LoanSimulationTotals {
  final String principal;
  final String interest;
  final String payment;
  final String finalBalance;

  LoanSimulationTotals({
    required this.principal,
    required this.interest,
    required this.payment,
    required this.finalBalance,
  });

  factory LoanSimulationTotals.fromJson(Map<String, dynamic> json) {
    return LoanSimulationTotals(
      principal: json['principal'].toString(),
      interest: json['interest'].toString(),
      payment: json['payment'].toString(),
      finalBalance: json['final_balance'].toString(),
    );
  }
}

class LoanSimulation {
  final String simulationToken;
  final String calculationVersion;
  final String principal;
  final String monthlyRate;
  final int installments;
  final List<LoanSimulationInstallment> schedule;
  final LoanSimulationTotals totals;
  final DateTime? expiresAt;

  LoanSimulation({
    required this.simulationToken,
    required this.calculationVersion,
    required this.principal,
    required this.monthlyRate,
    required this.installments,
    required this.schedule,
    required this.totals,
    this.expiresAt,
  });

  factory LoanSimulation.fromJson(Map<String, dynamic> json) {
    final schedule = (json['installments_schedule'] as List<dynamic>? ?? [])
        .map((item) => LoanSimulationInstallment.fromJson(
              Map<String, dynamic>.from(item as Map),
            ))
        .toList();
    return LoanSimulation(
      simulationToken: json['simulation_token'].toString(),
      calculationVersion: json['calculation_version'].toString(),
      principal: json['principal'].toString(),
      monthlyRate: json['monthly_rate'].toString(),
      installments: json['installments'] as int,
      schedule: schedule,
      totals: LoanSimulationTotals.fromJson(
        Map<String, dynamic>.from(json['totals'] as Map),
      ),
      expiresAt: json['expires_at'] == null
          ? null
          : DateTime.tryParse(json['expires_at'].toString()),
    );
  }
}
