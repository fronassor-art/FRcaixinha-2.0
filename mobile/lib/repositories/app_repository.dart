import '../services/api_client.dart';
import '../models/finance_models.dart';

class AppRepository {
  final ApiClient api;
  AppRepository(this.api);

  Future<Map<String, dynamic>> profile() => api.get('/members/me');
  Future<Map<String, dynamic>> contributionSummary() => api.get('/contributions/summary');
  Future<Map<String, dynamic>> contributions() => api.get('/contributions');
  Future<Map<String, dynamic>> notifications() => api.get('/notifications');
  Future<Map<String, dynamic>> loans() => api.get('/loans');
  Future<Map<String, dynamic>> loan(int id) => api.get('/loans/$id');
  Future<Map<String, dynamic>> createInstallmentPix(int installmentId) => api.post('/loan-installments/$installmentId/pix', {});
  Future<Map<String, dynamic>> installmentPayment(int installmentId) => api.get('/loan-installments/$installmentId/payment');
  Future<Map<String, dynamic>> statement() => api.get('/members/me/statement');

  Future<List<FinancialObligation>> financialObligations() async {
    final response = await api.get('/members/me/obligations');
    final items = response['items'] as List<dynamic>? ?? const [];
    return items.map((item) => FinancialObligation.fromJson(Map<String, dynamic>.from(item as Map))).toList();
  }

  Future<List<AdminDelinquencyItem>> adminDelinquency({
    FinancialObligationType? obligationType,
    FinancialObligationStatus? status,
    int? memberId,
    bool? overdueOnly,
    DateTime? dueFrom,
    DateTime? dueTo,
  }) async {
    String typeValue(FinancialObligationType value) =>
        value == FinancialObligationType.loanInstallment ? 'LOAN_INSTALLMENT' : 'CONTRIBUTION';
    String statusValue(FinancialObligationStatus value) => value.name.toUpperCase();
    String dateValue(DateTime value) => value.toIso8601String().split('T').first;
    final queryParameters = <String, String>{
      if (obligationType != null) 'obligation_type': typeValue(obligationType),
      if (status != null) 'status': statusValue(status),
      if (memberId != null) 'member_id': '$memberId',
      if (overdueOnly != null) 'overdue_only': '$overdueOnly',
      if (dueFrom != null) 'due_from': dateValue(dueFrom),
      if (dueTo != null) 'due_to': dateValue(dueTo),
    };
    final path = Uri(
      path: '/admin/delinquency',
      queryParameters: queryParameters.isEmpty ? null : queryParameters,
    ).toString();
    final response = await api.get(path);
    final items = response['items'] as List<dynamic>? ?? const [];
    return items
        .map((item) => AdminDelinquencyItem.fromJson(Map<String, dynamic>.from(item as Map)))
        .toList();
  }

  Future<AdminDelinquencySummary> adminDelinquencySummary() async =>
      AdminDelinquencySummary.fromJson(await api.get('/admin/delinquency/summary'));

  Future<PixPaymentStatusDetails> paymentStatus(int paymentId) async => PixPaymentStatusDetails.fromJson(await api.get('/payments/$paymentId'));
  Future<PixReceipt> paymentReceipt(int paymentId) async => PixReceipt.fromJson(await api.get('/payments/$paymentId/receipt'));
  Future<PixPayment> createContributionPix(int contributionId) async => PixPayment.fromJson(await api.postEmpty('/payments/pix/$contributionId'));
  Future<PixPayment> createLoanInstallmentPix(int installmentId) async => PixPayment.fromJson(await api.post('/loan-installments/$installmentId/pix', {}));

  Future<LoanSimulation> simulateLoan({
    required String principal,
    required int installments,
  }) async {
    final response = await api.post('/loans/simulations', {
      'principal': principal,
      'installments': installments,
    });
    return LoanSimulation.fromJson(response);
  }

  Future<void> confirmLoanSimulation(String simulationToken) async {
    await api.post('/loans/simulations/confirm', {'simulation_token': simulationToken});
  }

  Future<Map<String, dynamic>> requestLoan({
    required String principal,
    required int installments,
    required String simulationToken,
  }) => api.post('/loans', {
    'principal': principal,
    'installments': installments,
    'simulation_token': simulationToken,
  });
}
