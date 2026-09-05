import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../app.dart';

class LoanDetailScreen extends StatefulWidget {
  final int loanId;
  const LoanDetailScreen({super.key, required this.loanId});
  @override State<LoanDetailScreen> createState() => _LoanDetailScreenState();
}
class _LoanDetailScreenState extends State<LoanDetailScreen> {
  Map<String,dynamic>? data; String? error;
  @override void initState(){super.initState(); load();}
  Future<void> load() async { try { final r=await context.read<AppState>().repository.loan(widget.loanId); if(mounted)setState(()=>data=r); } catch(e){if(mounted)setState(()=>error=e.toString());} }
  Future<void> pay(Map<String,dynamic> i) async {
    try {
      final r=await context.read<AppState>().repository.createInstallmentPix(i['id'] as int);
      if(!mounted)return;
      final qr=(r['qr_code'] ?? '').toString();
      showDialog(context:context,builder:(_)=>AlertDialog(title:Text('Pix da parcela ${i['number']}'),content:SingleChildScrollView(child:SelectableText(qr.isEmpty?(r['ticket_url'] ?? 'Cobrança criada. Aguarde a confirmação.').toString():qr)),actions:[TextButton(onPressed:()=>Navigator.pop(context),child:const Text('Fechar'))]));
    } catch(e){if(mounted)ScaffoldMessenger.of(context).showSnackBar(SnackBar(content:Text(e.toString())));}
  }
  @override Widget build(BuildContext context){
    if(error!=null)return Scaffold(appBar:AppBar(title:const Text('Empréstimo')),body:Center(child:Text(error!)));
    if(data==null)return const Scaffold(body:Center(child:CircularProgressIndicator()));
    final l=data!; final items=l['installments'] as List<dynamic>? ?? [];
    return Scaffold(appBar:AppBar(title:Text('Empréstimo #${l['id']}')),body:ListView(padding:const EdgeInsets.all(16),children:[
      Card(child:Padding(padding:const EdgeInsets.all(16),child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[Text('Status: ${l['status']}'),Text('Principal: R\$ ${l['principal']}'),Text('Taxa mensal: ${l['monthly_rate']}'),Text('Parcelas: ${l['installments']}')]))),
      const SizedBox(height:12),const Text('Parcelas',style:TextStyle(fontSize:20,fontWeight:FontWeight.bold)),
      ...items.map((x){final i=x as Map<String,dynamic>; final paid=i['status']=='PAID'; return Card(child:ListTile(title:Text('Parcela ${i['number']} • R\$ ${i['amount']}'),subtitle:Text('Vencimento: ${i['due_date']} • ${i['status']}\nPago R\$ ${i['paid_amount']}'),isThreeLine:true,trailing:paid?const Icon(Icons.check_circle):ElevatedButton(onPressed:()=>pay(i),child:const Text('Pagar Pix'))));})
    ]));
  }
}
