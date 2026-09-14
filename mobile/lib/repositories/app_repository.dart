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
