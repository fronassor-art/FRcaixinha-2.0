import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../models/finance_models.dart';
import '../services/api_client.dart';
import '../services/session.dart';

class ContributionsScreen extends StatefulWidget { const ContributionsScreen({super.key}); @override State<ContributionsScreen> createState()=>_ContributionsScreenState(); }
class _ContributionsScreenState extends State<ContributionsScreen> {
  final api=ApiClient(); List<ContributionItem> items=[]; Map<String,dynamic>? summary; bool loading=true; String? error;
  @override void initState(){super.initState(); load();}
  Future<void> load() async { api.token=await Session.getToken(); setState(()=>loading=true); try { final a=await api.get('/contributions'); final b=await api.get('/contributions/summary'); if(mounted)setState(() { items=(a['items'] as List).map((e)=>ContributionItem.fromJson(Map<String,dynamic>.from(e))).toList(); summary=b; error=null; }); } catch(e){if(mounted)setState(()=>error=e.toString());} finally{if(mounted)setState(()=>loading=false);} }
  Future<void> pay(ContributionItem c) async { api.token=await Session.getToken(); try { final j=await api.postEmpty('/payments/pix/${c.id}'); final pix=PixPayment.fromJson(j); if(!mounted)return; await Navigator.push(context,MaterialPageRoute(builder:(_)=>PixScreen(api:api,pix:pix,contributionId:c.id))); await load(); } catch(e){ if(mounted)ScaffoldMessenger.of(context).showSnackBar(SnackBar(content:Text(e.toString()))); } }
  @override Widget build(BuildContext context){ return Scaffold(appBar:AppBar(title:const Text('Contribuições')),body:RefreshIndicator(onRefresh:load,child:ListView(padding:const EdgeInsets.all(16),children:[
    if(summary!=null) Card(child:Padding(padding:const EdgeInsets.all(16),child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[Text('Total pago: R$ ${summary!['paid_total']}'),Text('Pendente: R$ ${summary!['pending_total']}'),Text('Plano: R$ ${summary!['expected_total']}')]))) ,
    if(error!=null) Text(error!), if(loading) const Center(child:CircularProgressIndicator()),
    ...items.map((c)=>Card(child:ListTile(title:Text('Competência ${c.competence.substring(0,7)}'),subtitle:Text('R$ ${c.amount} • ${c.status}'),trailing:c.status=='PAID'?const Icon(Icons.check_circle):ElevatedButton(onPressed:()=>pay(c),child:const Text('Pagar Pix')))))
  ]))); }
}

class PixScreen extends StatefulWidget { final ApiClient api; final PixPayment pix; final int contributionId; const PixScreen({super.key,required this.api,required this.pix,required this.contributionId}); @override State<PixScreen> createState()=>_PixScreenState(); }
class _PixScreenState extends State<PixScreen>{ Timer? timer; String status='PENDING'; String contributionStatus='PENDING';
 @override void initState(){super.initState(); status=widget.pix.status; if(status!='approved') timer=Timer.periodic(const Duration(seconds:5),(_)=>refresh());}
 @override void dispose(){timer?.cancel();super.dispose();}
 Future<void> refresh() async { try{final j=await widget.api.get('/payments/contribution/${widget.contributionId}'); final p=j['payment']; if(p!=null){setState(() { status='${p['status']}'; contributionStatus='${j['contribution_status']}'; }); if(contributionStatus=='PAID'||status=='approved')timer?.cancel();}}catch(_){} }
 @override Widget build(BuildContext context){final b64=widget.pix.qrCodeBase64; final paid=contributionStatus=='PAID'||status=='approved'; return Scaffold(appBar:AppBar(title:const Text('Pagamento Pix')),body:ListView(padding:const EdgeInsets.all(20),children:[Text('Valor: R$ ${widget.pix.amount}',style:Theme.of(context).textTheme.titleLarge),const SizedBox(height:16),Center(child:paid?const Icon(Icons.check_circle,size:96):b64!=null?Image.memory(base64Decode(b64),width:260,height:260):const Icon(Icons.qr_code_2,size:180)),const SizedBox(height:16),Center(child:Text(paid?'Pagamento confirmado!':'Aguardando confirmação: $status',style:Theme.of(context).textTheme.titleMedium)),if(!paid&&widget.pix.qrCode!=null) ...[const SizedBox(height:20),SelectableText(widget.pix.qrCode!),ElevatedButton.icon(onPressed:()async{await Clipboard.setData(ClipboardData(text:widget.pix.qrCode!));if(mounted)ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content:Text('Código Pix copiado.')));},icon:const Icon(Icons.copy),label:const Text('Copiar código Pix'))],const SizedBox(height:12),OutlinedButton(onPressed:refresh,child:const Text('Atualizar status'))])); }
}
