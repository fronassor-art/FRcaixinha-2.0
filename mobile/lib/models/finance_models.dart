class ContributionItem {
  final int id; final String competence; final String amount; final String status; final Map<String,dynamic>? payment; final DateTime? dueDate; final String paidAmount;
  ContributionItem({required this.id, required this.competence, required this.amount, required this.status, this.payment, this.dueDate, this.paidAmount='0.00'});
  factory ContributionItem.fromJson(Map<String,dynamic> j) => ContributionItem(
    id: j['id'], competence: j['competence'], amount: j['amount'], status: j['status'],
    payment: j['payment'] == null ? null : Map<String,dynamic>.from(j['payment']),
    dueDate: j['due_date']==null?null:DateTime.tryParse('${j['due_date']}'), paidAmount: '${j['paid_amount']??'0.00'}',
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

enum PixProviderStatus { pending, inProcess, approved, cancelled, rejected, refunded, chargedBack, unknown }
PixProviderStatus pixProviderStatusFromJson(Object? value) {
  switch (value?.toString().toLowerCase()) {
    case 'pending': return PixProviderStatus.pending;
    case 'in_process': return PixProviderStatus.inProcess;
    case 'approved': return PixProviderStatus.approved;
    case 'cancelled': return PixProviderStatus.cancelled;
    case 'rejected': return PixProviderStatus.rejected;
    case 'refunded': return PixProviderStatus.refunded;
    case 'charged_back': return PixProviderStatus.chargedBack;
    default: return PixProviderStatus.unknown;
  }
}
class PixPaymentStatusDetails {
  final int paymentId; final String providerPaymentId, amount; final PixProviderStatus status;
  final FinancialObligationStatus? obligationStatus; final String? contributionStatus, installmentStatus;
  const PixPaymentStatusDetails({required this.paymentId, required this.providerPaymentId, required this.amount, required this.status, this.obligationStatus, this.contributionStatus, this.installmentStatus});
  bool get isConfirmed => status == PixProviderStatus.approved;
  bool get isFinal => isConfirmed || status == PixProviderStatus.cancelled || status == PixProviderStatus.rejected || status == PixProviderStatus.refunded || status == PixProviderStatus.chargedBack;
  bool get obligationIsPaid => obligationStatus == FinancialObligationStatus.paid;
  factory PixPaymentStatusDetails.fromJson(Map<String, dynamic> json) { int id(Object? v) => v is int ? v : int.tryParse('$v') ?? 0; return PixPaymentStatusDetails(paymentId: id(json['payment_id']), providerPaymentId: '${json['provider_payment_id'] ?? ''}', amount: '${json['amount'] ?? '0.00'}', status: pixProviderStatusFromJson(json['status']), obligationStatus: json['obligation_status'] == null ? null : financialObligationStatusFromJson(json['obligation_status']), contributionStatus: json['contribution_status']?.toString(), installmentStatus: json['installment_status']?.toString()); }
}
class PixReceiptPayment { final int? id; final String? provider,providerOrderId,providerPaymentId,externalReference,pixTxid,endToEndId,referenceType,referenceId; const PixReceiptPayment({this.id,this.provider,this.providerOrderId,this.providerPaymentId,this.externalReference,this.pixTxid,this.endToEndId,this.referenceType,this.referenceId}); factory PixReceiptPayment.fromJson(Map j)=>PixReceiptPayment(id:j['id'] is int?j['id']:int.tryParse('${j['id']}'),provider:j['provider']?.toString(),providerOrderId:j['provider_order_id']?.toString(),providerPaymentId:j['provider_payment_id']?.toString(),externalReference:j['external_reference']?.toString(),pixTxid:j['pix_txid']?.toString(),endToEndId:j['end_to_end_id']?.toString(),referenceType:j['reference_type']?.toString(),referenceId:j['reference_id']?.toString()); }
class PixReceiptObligation { final String? type,statusBefore,statusAfter; final int? contributionId,loanInstallmentId,memberId; const PixReceiptObligation({this.type,this.contributionId,this.loanInstallmentId,this.memberId,this.statusBefore,this.statusAfter}); factory PixReceiptObligation.fromJson(Map j)=>PixReceiptObligation(type:j['type']?.toString(),contributionId:j['contribution_id'] is int?j['contribution_id']:int.tryParse('${j['contribution_id']}'),loanInstallmentId:j['loan_installment_id'] is int?j['loan_installment_id']:int.tryParse('${j['loan_installment_id']}'),memberId:j['member_id'] is int?j['member_id']:int.tryParse('${j['member_id']}'),statusBefore:j['status_before']?.toString(),statusAfter:j['status_after']?.toString()); }
class PixReceiptAmounts { final String received,applied,principal,interest,penalty,excess; const PixReceiptAmounts({required this.received,required this.applied,required this.principal,required this.interest,required this.penalty,required this.excess}); factory PixReceiptAmounts.fromJson(Map j)=>PixReceiptAmounts(received:'${j['received']??'0.00'}',applied:'${j['applied']??'0.00'}',principal:'${j['principal']??'0.00'}',interest:'${j['interest']??'0.00'}',penalty:'${j['penalty']??'0.00'}',excess:'${j['excess']??'0.00'}'); }
class PixReceiptConfirmation { final String? source,confirmedAt; final int? webhookEventId; const PixReceiptConfirmation({this.source,this.confirmedAt,this.webhookEventId}); factory PixReceiptConfirmation.fromJson(Map j)=>PixReceiptConfirmation(source:j['source']?.toString(),confirmedAt:j['confirmed_at']?.toString(),webhookEventId:j['webhook_event_id'] is int?j['webhook_event_id']:int.tryParse('${j['webhook_event_id']}')); }
class PixReceipt { final String receiptVersion,receiptNumber; final PixReceiptPayment payment; final PixReceiptObligation obligation; final PixReceiptAmounts amounts; final PixReceiptConfirmation confirmation; final List<Map<String,dynamic>> ledgerEntries; const PixReceipt({required this.receiptVersion,required this.receiptNumber,required this.payment,required this.obligation,required this.amounts,required this.confirmation,required this.ledgerEntries}); factory PixReceipt.fromJson(Map<String,dynamic> j)=>PixReceipt(receiptVersion:'${j['receipt_version']??''}',receiptNumber:'${j['receipt_number']??''}',payment:PixReceiptPayment.fromJson(j['payment'] as Map? ?? const {}),obligation:PixReceiptObligation.fromJson(j['obligation'] as Map? ?? const {}),amounts:PixReceiptAmounts.fromJson(j['amounts'] as Map? ?? const {}),confirmation:PixReceiptConfirmation.fromJson(j['confirmation'] as Map? ?? const {}),ledgerEntries:(j['ledger_entries'] as List<dynamic>? ?? const []).map((x)=>Map<String,dynamic>.from(x as Map)).toList()); }
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
