import '../services/api_client.dart';

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
  Future<Map<String, dynamic>> requestLoan({required String principal, required String monthlyRate, required int installments}) =>
      api.post('/loans', {
        'principal': principal,
        'monthly_rate': monthlyRate,
        'installments': installments,
      });
}
