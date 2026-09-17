class ContributionItem {
  final int id;
  final String competence;
  final String amount;
  final String status;
  final Map<String, dynamic>? payment;
  final DateTime? dueDate;
  final String paidAmount;
  ContributionItem({
    required this.id,
    required this.competence,
    required this.amount,
    required this.status,
    this.payment,
    this.dueDate,
    this.paidAmount = '0.00',
  });
  factory ContributionItem.fromJson(Map<String, dynamic> j) => ContributionItem(
    id: j['id'],
    competence: j['competence'],
    amount: j['amount'],
    status: j['status'],
    payment:
        j['payment'] == null ? null : Map<String, dynamic>.from(j['payment']),
    dueDate:
        j['due_date'] == null ? null : DateTime.tryParse('${j['due_date']}'),
    paidAmount: '${j['paid_amount'] ?? '0.00'}',
  );
}

enum StatementMovementDirection { credit, debit, unknown }

StatementMovementDirection statementMovementDirectionFromJson(Object? value) {
  switch (value?.toString().toUpperCase()) {
    case 'CREDIT':
      return StatementMovementDirection.credit;
    case 'DEBIT':
      return StatementMovementDirection.debit;
    default:
      return StatementMovementDirection.unknown;
  }
}

class MemberStatementTotals {
  final String contributionsPaid, loanPayments, loanOutstanding;
  const MemberStatementTotals({
    required this.contributionsPaid,
    required this.loanPayments,
    required this.loanOutstanding,
  });
  factory MemberStatementTotals.fromJson(Map<String, dynamic>? json) {
    String money(Object? value) => value?.toString() ?? '0.00';
    return MemberStatementTotals(
      contributionsPaid: money(json?['contributions_paid']),
      loanPayments: money(json?['loan_payments']),
      loanOutstanding: money(json?['loan_outstanding']),
    );
  }
}

class MemberStatementMovement {
  final String id, type, description, total;
  final StatementMovementDirection direction;
  final DateTime? occurredAt;
  final String? competence, principal, interest, penalty;
  final int? loanId, installmentNumber, paymentId;
  final bool receiptAvailable;
  const MemberStatementMovement({
    required this.id,
    required this.type,
    required this.direction,
    required this.occurredAt,
    required this.description,
    required this.total,
    required this.competence,
    required this.loanId,
    required this.installmentNumber,
    required this.principal,
    required this.interest,
    required this.penalty,
    required this.paymentId,
    required this.receiptAvailable,
  });
  factory MemberStatementMovement.fromJson(Map<String, dynamic> json) {
    int? integer(Object? value) =>
        value is int ? value : int.tryParse('$value');
    String? money(Object? value) => value == null ? null : value.toString();
    return MemberStatementMovement(
      id: json['id']?.toString() ?? '',
      type: json['type']?.toString() ?? '',
      direction: statementMovementDirectionFromJson(json['direction']),
      occurredAt:
          json['occurred_at'] == null
              ? null
              : DateTime.tryParse('${json['occurred_at']}'),
      description: json['description']?.toString() ?? '',
      total: json['total']?.toString() ?? '0.00',
      competence: json['competence']?.toString(),
      loanId: integer(json['loan_id']),
      installmentNumber: integer(json['installment_number']),
      principal: money(json['principal']),
      interest: money(json['interest']),
      penalty: money(json['penalty']),
      paymentId: integer(json['payment_id']),
      receiptAvailable: json['receipt_available'] == true,
    );
  }
}

class MemberStatement {
  final MemberStatementTotals totals;
  final List<MemberStatementMovement> movements;
  const MemberStatement({required this.totals, required this.movements});
  factory MemberStatement.fromJson(Map<String, dynamic> json) {
    final rawMovements = json['movements'] as List<dynamic>? ?? const [];
    final rawTotals = json['totals'];
    return MemberStatement(
      totals: MemberStatementTotals.fromJson(
        rawTotals is Map<String, dynamic>
            ? rawTotals
            : rawTotals is Map
            ? Map<String, dynamic>.from(rawTotals)
            : null,
      ),
      movements:
          rawMovements
              .whereType<Map>()
              .map(
                (item) => MemberStatementMovement.fromJson(
                  Map<String, dynamic>.from(item),
                ),
              )
              .toList(),
    );
  }
}

class PixPayment {
  final int paymentId;
  final String providerPaymentId;
  final String status;
  final String amount;
  final String? qrCode;
  final String? qrCodeBase64;
  final String? ticketUrl;
  PixPayment({
    required this.paymentId,
    required this.providerPaymentId,
    required this.status,
    required this.amount,
    this.qrCode,
    this.qrCodeBase64,
    this.ticketUrl,
  });
  factory PixPayment.fromJson(Map<String, dynamic> j) => PixPayment(
    paymentId: j['payment_id'],
    providerPaymentId: '${j['provider_payment_id']}',
    status: '${j['status']}',
    amount: '${j['amount']}',
    qrCode: j['qr_code'],
    qrCodeBase64: j['qr_code_base64'],
    ticketUrl: j['ticket_url'],
  );
}

enum FinancialObligationType {
  contribution,
  loanInstallment,
  agreementInstallment,
  unknown,
}

enum FinancialObligationStatus { pending, partial, overdue, paid }

FinancialObligationType financialObligationTypeFromJson(Object? value) {
  switch (value?.toString().toUpperCase()) {
    case 'CONTRIBUTION':
      return FinancialObligationType.contribution;
    case 'LOAN_INSTALLMENT':
      return FinancialObligationType.loanInstallment;
    case 'AGREEMENT_INSTALLMENT':
      return FinancialObligationType.agreementInstallment;
    default:
      return FinancialObligationType.unknown;
  }
}

FinancialObligationStatus financialObligationStatusFromJson(Object? value) {
  switch (value?.toString().toUpperCase()) {
    case 'PARTIAL':
      return FinancialObligationStatus.partial;
    case 'OVERDUE':
      return FinancialObligationStatus.overdue;
    case 'PAID':
      return FinancialObligationStatus.paid;
    default:
      return FinancialObligationStatus.pending;
  }
}

class FinancialObligation {
  final int memberId, obligationId, daysOverdue;
  final FinancialObligationType type;
  final String? competence;
  final int? loanId, installmentNumber, paymentId;
  final DateTime? dueDate;
  final String amountDue,
      amountPaid,
      outstandingAmount,
      principalOutstanding,
      interestOutstanding,
      penaltyOutstanding;
  final FinancialObligationStatus financialStatus;
  final bool receiptAvailable;
  const FinancialObligation({
    required this.memberId,
    required this.type,
    required this.obligationId,
    required this.competence,
    required this.loanId,
    required this.installmentNumber,
    required this.dueDate,
    required this.amountDue,
    required this.amountPaid,
    required this.outstandingAmount,
    required this.financialStatus,
    required this.daysOverdue,
    required this.principalOutstanding,
    required this.interestOutstanding,
    required this.penaltyOutstanding,
    required this.paymentId,
    required this.receiptAvailable,
  });
  factory FinancialObligation.fromJson(Map<String, dynamic> json) {
    int? integer(Object? value) =>
        value is int ? value : int.tryParse('$value');
    String money(Object? value) => value?.toString() ?? '0.00';
    return FinancialObligation(
      memberId: integer(json['member_id']) ?? 0,
      type: financialObligationTypeFromJson(json['obligation_type']),
      obligationId: integer(json['obligation_id']) ?? 0,
      competence: json['competence']?.toString(),
      loanId: integer(json['loan_id']),
      installmentNumber: integer(json['installment_number']),
      dueDate:
          json['due_date'] == null
              ? null
              : DateTime.tryParse('${json['due_date']}'),
      amountDue: money(json['amount_due']),
      amountPaid: money(json['amount_paid']),
      outstandingAmount: money(json['outstanding_amount']),
      financialStatus: financialObligationStatusFromJson(
        json['financial_status'],
      ),
      daysOverdue: integer(json['days_overdue']) ?? 0,
      principalOutstanding: money(json['principal_outstanding']),
      interestOutstanding: money(json['interest_outstanding']),
      penaltyOutstanding: money(json['penalty_outstanding']),
      paymentId: integer(json['payment_id']),
      receiptAvailable: json['receipt_available'] == true,
    );
  }
}

/// Obligation with member identification, returned by the administrative
/// delinquency endpoint.
class AdminDelinquencyItem {
  final int memberId;
  final String? memberName;
  final FinancialObligationType obligationType;
  final int obligationId;
  final String? competence;
  final int? loanId;
  final int? installmentNumber;
  final DateTime? dueDate;
  final String amountDue;
  final String amountPaid;
  final String outstandingAmount;
  final FinancialObligationStatus status;
  final int daysOverdue;
  final String principalOutstanding;
  final String interestOutstanding;
  final String penaltyOutstanding;
  final int? paymentId;
  final bool receiptAvailable;

  const AdminDelinquencyItem({
    required this.memberId,
    required this.memberName,
    required this.obligationType,
    required this.obligationId,
    required this.competence,
    required this.loanId,
    required this.installmentNumber,
    required this.dueDate,
    required this.amountDue,
    required this.amountPaid,
    required this.outstandingAmount,
    required this.status,
    required this.daysOverdue,
    required this.principalOutstanding,
    required this.interestOutstanding,
    required this.penaltyOutstanding,
    required this.paymentId,
    required this.receiptAvailable,
  });

  factory AdminDelinquencyItem.fromJson(Map<String, dynamic> json) {
    int? integer(Object? value) =>
        value is int ? value : int.tryParse('$value');
    String money(Object? value) => value?.toString() ?? '0.00';
    return AdminDelinquencyItem(
      memberId: integer(json['member_id']) ?? 0,
      memberName: json['member_name']?.toString(),
      obligationType: financialObligationTypeFromJson(json['obligation_type']),
      obligationId: integer(json['obligation_id']) ?? 0,
      competence: json['competence']?.toString(),
      loanId: integer(json['loan_id']),
      installmentNumber: integer(json['installment_number']),
      dueDate:
          json['due_date'] == null
              ? null
              : DateTime.tryParse('${json['due_date']}'),
      amountDue: money(json['amount_due']),
      amountPaid: money(json['amount_paid']),
      outstandingAmount: money(
        json['outstanding_amount'] ?? json['outstanding'],
      ),
      status: financialObligationStatusFromJson(
        json['financial_status'] ?? json['status'],
      ),
      daysOverdue: integer(json['days_overdue']) ?? 0,
      principalOutstanding: money(json['principal_outstanding']),
      interestOutstanding: money(json['interest_outstanding']),
      penaltyOutstanding: money(json['penalty_outstanding']),
      paymentId: integer(json['payment_id']),
      receiptAvailable: json['receipt_available'] == true,
    );
  }
}

class AdminDelinquencySummary {
  final int pendingCount;
  final int partialCount;
  final int overdueCount;
  final int paidCount;
  final String totalOutstanding;
  final String totalOverdue;
  final String totalPartialOutstanding;
  final int totalContributions;
  final int totalInstallments;
  final int delinquentMembersCount;
  final String totalInterestOutstanding;
  final String totalPenaltyOutstanding;

  const AdminDelinquencySummary({
    required this.pendingCount,
    required this.partialCount,
    required this.overdueCount,
    required this.paidCount,
    required this.totalOutstanding,
    required this.totalOverdue,
    required this.totalPartialOutstanding,
    required this.totalContributions,
    required this.totalInstallments,
    required this.delinquentMembersCount,
    required this.totalInterestOutstanding,
    required this.totalPenaltyOutstanding,
  });

  factory AdminDelinquencySummary.fromJson(Map<String, dynamic> json) {
    final counts =
        json['counts'] is Map
            ? Map<String, dynamic>.from(json['counts'] as Map)
            : const <String, dynamic>{};
    final byType =
        json['by_type'] is Map
            ? Map<String, dynamic>.from(json['by_type'] as Map)
            : const <String, dynamic>{};
    int integer(Object? value) =>
        value is int ? value : int.tryParse('$value') ?? 0;
    String money(Object? value) => value?.toString() ?? '0.00';
    return AdminDelinquencySummary(
      pendingCount: integer(json['pending_count'] ?? counts['PENDING']),
      partialCount: integer(json['partial_count'] ?? counts['PARTIAL']),
      overdueCount: integer(json['overdue_count'] ?? counts['OVERDUE']),
      paidCount: integer(json['paid_count'] ?? counts['PAID']),
      totalOutstanding: money(json['total_outstanding']),
      totalOverdue: money(json['total_overdue']),
      totalPartialOutstanding: money(json['total_partial_outstanding']),
      totalContributions: integer(
        json['total_contributions'] ?? byType['contributions'],
      ),
      totalInstallments: integer(json['total_installments'] ?? byType['loans']),
      delinquentMembersCount: integer(json['delinquent_members_count']),
      totalInterestOutstanding: money(json['total_interest_outstanding']),
      totalPenaltyOutstanding: money(json['total_penalty_outstanding']),
    );
  }
}

enum PixProviderStatus {
  pending,
  inProcess,
  approved,
  cancelled,
  rejected,
  refunded,
  chargedBack,
  unknown,
}

PixProviderStatus pixProviderStatusFromJson(Object? value) {
  switch (value?.toString().toLowerCase()) {
    case 'pending':
      return PixProviderStatus.pending;
    case 'in_process':
      return PixProviderStatus.inProcess;
    case 'approved':
      return PixProviderStatus.approved;
    case 'cancelled':
      return PixProviderStatus.cancelled;
    case 'rejected':
      return PixProviderStatus.rejected;
    case 'refunded':
      return PixProviderStatus.refunded;
    case 'charged_back':
      return PixProviderStatus.chargedBack;
    default:
      return PixProviderStatus.unknown;
  }
}

class PixPaymentStatusDetails {
  final int paymentId;
  final String providerPaymentId, amount;
  final PixProviderStatus status;
  final FinancialObligationStatus? obligationStatus;
  final String? contributionStatus, installmentStatus;
  const PixPaymentStatusDetails({
    required this.paymentId,
    required this.providerPaymentId,
    required this.amount,
    required this.status,
    this.obligationStatus,
    this.contributionStatus,
    this.installmentStatus,
  });
  bool get isConfirmed => status == PixProviderStatus.approved;
  bool get isFinal =>
      isConfirmed ||
      status == PixProviderStatus.cancelled ||
      status == PixProviderStatus.rejected ||
      status == PixProviderStatus.refunded ||
      status == PixProviderStatus.chargedBack;
  bool get obligationIsPaid =>
      obligationStatus == FinancialObligationStatus.paid;
  factory PixPaymentStatusDetails.fromJson(Map<String, dynamic> json) {
    int id(Object? v) => v is int ? v : int.tryParse('$v') ?? 0;
    return PixPaymentStatusDetails(
      paymentId: id(json['payment_id']),
      providerPaymentId: '${json['provider_payment_id'] ?? ''}',
      amount: '${json['amount'] ?? '0.00'}',
      status: pixProviderStatusFromJson(json['status']),
      obligationStatus:
          json['obligation_status'] == null
              ? null
              : financialObligationStatusFromJson(json['obligation_status']),
      contributionStatus: json['contribution_status']?.toString(),
      installmentStatus: json['installment_status']?.toString(),
    );
  }
}

class PixReceiptPayment {
  final int? id;
  final String? provider,
      providerOrderId,
      providerPaymentId,
      externalReference,
      pixTxid,
      endToEndId,
      referenceType,
      referenceId;
  const PixReceiptPayment({
    this.id,
    this.provider,
    this.providerOrderId,
    this.providerPaymentId,
    this.externalReference,
    this.pixTxid,
    this.endToEndId,
    this.referenceType,
    this.referenceId,
  });
  factory PixReceiptPayment.fromJson(Map j) => PixReceiptPayment(
    id: j['id'] is int ? j['id'] : int.tryParse('${j['id']}'),
    provider: j['provider']?.toString(),
    providerOrderId: j['provider_order_id']?.toString(),
    providerPaymentId: j['provider_payment_id']?.toString(),
    externalReference: j['external_reference']?.toString(),
    pixTxid: j['pix_txid']?.toString(),
    endToEndId: j['end_to_end_id']?.toString(),
    referenceType: j['reference_type']?.toString(),
    referenceId: j['reference_id']?.toString(),
  );
}

class PixReceiptObligation {
  final String? type, statusBefore, statusAfter;
  final int? contributionId, loanInstallmentId, memberId;
  const PixReceiptObligation({
    this.type,
    this.contributionId,
    this.loanInstallmentId,
    this.memberId,
    this.statusBefore,
    this.statusAfter,
  });
  factory PixReceiptObligation.fromJson(Map j) => PixReceiptObligation(
    type: j['type']?.toString(),
    contributionId:
        j['contribution_id'] is int
            ? j['contribution_id']
            : int.tryParse('${j['contribution_id']}'),
    loanInstallmentId:
        j['loan_installment_id'] is int
            ? j['loan_installment_id']
            : int.tryParse('${j['loan_installment_id']}'),
    memberId:
        j['member_id'] is int
            ? j['member_id']
            : int.tryParse('${j['member_id']}'),
    statusBefore: j['status_before']?.toString(),
    statusAfter: j['status_after']?.toString(),
  );
}

class PixReceiptAmounts {
  final String received, applied, principal, interest, penalty, excess;
  const PixReceiptAmounts({
    required this.received,
    required this.applied,
    required this.principal,
    required this.interest,
    required this.penalty,
    required this.excess,
  });
  factory PixReceiptAmounts.fromJson(Map j) => PixReceiptAmounts(
    received: '${j['received'] ?? '0.00'}',
    applied: '${j['applied'] ?? '0.00'}',
    principal: '${j['principal'] ?? '0.00'}',
    interest: '${j['interest'] ?? '0.00'}',
    penalty: '${j['penalty'] ?? '0.00'}',
    excess: '${j['excess'] ?? '0.00'}',
  );
}

class PixReceiptConfirmation {
  final String? source, confirmedAt;
  final int? webhookEventId;
  const PixReceiptConfirmation({
    this.source,
    this.confirmedAt,
    this.webhookEventId,
  });
  factory PixReceiptConfirmation.fromJson(Map j) => PixReceiptConfirmation(
    source: j['source']?.toString(),
    confirmedAt: j['confirmed_at']?.toString(),
    webhookEventId:
        j['webhook_event_id'] is int
            ? j['webhook_event_id']
            : int.tryParse('${j['webhook_event_id']}'),
  );
}

class PixReceipt {
  final String receiptVersion, receiptNumber;
  final PixReceiptPayment payment;
  final PixReceiptObligation obligation;
  final PixReceiptAmounts amounts;
  final PixReceiptConfirmation confirmation;
  final List<Map<String, dynamic>> ledgerEntries;
  const PixReceipt({
    required this.receiptVersion,
    required this.receiptNumber,
    required this.payment,
    required this.obligation,
    required this.amounts,
    required this.confirmation,
    required this.ledgerEntries,
  });
  factory PixReceipt.fromJson(Map<String, dynamic> j) => PixReceipt(
    receiptVersion: '${j['receipt_version'] ?? ''}',
    receiptNumber: '${j['receipt_number'] ?? ''}',
    payment: PixReceiptPayment.fromJson(j['payment'] as Map? ?? const {}),
    obligation: PixReceiptObligation.fromJson(
      j['obligation'] as Map? ?? const {},
    ),
    amounts: PixReceiptAmounts.fromJson(j['amounts'] as Map? ?? const {}),
    confirmation: PixReceiptConfirmation.fromJson(
      j['confirmation'] as Map? ?? const {},
    ),
    ledgerEntries:
        (j['ledger_entries'] as List<dynamic>? ?? const [])
            .map((x) => Map<String, dynamic>.from(x as Map))
            .toList(),
  );
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
    final schedule =
        (json['installments_schedule'] as List<dynamic>? ?? [])
            .map(
              (item) => LoanSimulationInstallment.fromJson(
                Map<String, dynamic>.from(item as Map),
              ),
            )
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
      expiresAt:
          json['expires_at'] == null
              ? null
              : DateTime.tryParse(json['expires_at'].toString()),
    );
  }
}
