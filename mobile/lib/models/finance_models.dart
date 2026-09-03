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
