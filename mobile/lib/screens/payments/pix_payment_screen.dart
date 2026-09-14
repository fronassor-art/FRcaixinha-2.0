import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../../models/finance_models.dart';
import '../../repositories/app_repository.dart';

class PixPaymentScreen extends StatefulWidget {
  final PixPayment payment; final AppRepository repository; final bool receiptAvailable; final Duration pollInterval; final ValueChanged<PixPaymentStatusDetails>? onPaymentUpdated; final VoidCallback? onPaymentConfirmed;
  const PixPaymentScreen({super.key, required this.payment, required this.repository, this.receiptAvailable = false, this.pollInterval = const Duration(seconds: 5), this.onPaymentUpdated, this.onPaymentConfirmed});
  @override State<PixPaymentScreen> createState() => _PixPaymentScreenState();
}
class _PixPaymentScreenState extends State<PixPaymentScreen> {
  Timer? _timer; PixPaymentStatusDetails? _status; PixReceipt? _receipt; Object? _error; bool _checking = false, _confirmedCallbackSent = false;
  @override void initState() { super.initState(); _startPolling(); _refresh(); }
  @override void dispose() { _timer?.cancel(); super.dispose(); }
  void _startPolling() { _timer?.cancel(); _timer = Timer.periodic(widget.pollInterval, (_) { if (_status?.isFinal != true) _refresh(); }); }
  Future<void> _refresh() async {
    if (_checking) return; _checking = true;
    try { final status = await widget.repository.paymentStatus(widget.payment.paymentId); if (!mounted) return; setState(() { _status = status; _error = null; }); widget.onPaymentUpdated?.call(status); if (status.isFinal) _timer?.cancel(); if (status.isConfirmed && !_confirmedCallbackSent) { _confirmedCallbackSent = true; widget.onPaymentConfirmed?.call(); } }
    catch (error) { if (mounted) setState(() => _error = error); }
    finally { _checking = false; }
  }
  Future<void> _loadReceipt() async {
    try { final receipt = await widget.repository.paymentReceipt(widget.payment.paymentId); if (mounted) setState(() { _receipt = receipt; _error = null; }); }
    catch (error) { if (mounted) setState(() => _error = error); }
  }
  String get _statusText => _status?.status.name ?? widget.payment.status;
  @override Widget build(BuildContext context) {
    final code = widget.payment.qrCode; final base64 = widget.payment.qrCodeBase64; final confirmed = _status?.isConfirmed == true; final paid = _status?.obligationIsPaid == true;
    return Scaffold(appBar: AppBar(title: const Text('Pagamento Pix')), body: ListView(padding: const EdgeInsets.all(20), children: [
      Text('Valor: R\$ ${widget.payment.amount}', style: Theme.of(context).textTheme.titleLarge), const SizedBox(height: 16),
      if (base64 != null && base64.isNotEmpty) Center(child: Image.memory(base64Decode(base64), width: 240, height: 240)) else const Center(child: Icon(Icons.qr_code_2, size: 160)),
      const SizedBox(height: 16), Text(confirmed ? (paid ? 'Pagamento confirmado e obrigação quitada.' : 'Pagamento confirmado. A obrigação pode permanecer parcial.') : 'Status do pagamento: $_statusText'),
      if (code != null && code.isNotEmpty) ...[const SizedBox(height: 16), const Text('Pix copia e cola'), SelectableText(code), FilledButton.icon(onPressed: () async { await Clipboard.setData(ClipboardData(text: code)); if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Código Pix copiado.'))); }, icon: const Icon(Icons.copy), label: const Text('Copiar código Pix'))],
      if (widget.payment.ticketUrl != null && widget.payment.ticketUrl!.isNotEmpty) ...[const SizedBox(height: 12), const Text('Link da cobrança'), SelectableText(widget.payment.ticketUrl!)],
      if (widget.receiptAvailable && confirmed) OutlinedButton(onPressed: _loadReceipt, child: const Text('Consultar recibo')),
      if (_receipt != null) Card(child: Padding(padding: const EdgeInsets.all(12), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text('Recibo ${_receipt!.receiptNumber}'), Text('Recebido: R\$ ${_receipt!.amounts.received}'), Text('Aplicado: R\$ ${_receipt!.amounts.applied}'), Text('Estado após: ${_receipt!.obligation.statusAfter ?? '-'}')]))),
      if (_error != null) ...[Text('Não foi possível atualizar: $_error'), OutlinedButton(onPressed: _refresh, child: const Text('Tentar novamente'))],
      const SizedBox(height: 12), OutlinedButton(onPressed: _checking ? null : _refresh, child: const Text('Atualizar status')),
    ]));
  }
}
