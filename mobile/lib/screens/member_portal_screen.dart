import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../app.dart';

class MemberPortalScreen extends StatefulWidget {
  const MemberPortalScreen({super.key});
  @override State<MemberPortalScreen> createState() => _MemberPortalScreenState();
}

class _MemberPortalScreenState extends State<MemberPortalScreen> {
  Map<String,dynamic>? data; String? error;
  Future<void> load() async {
    try { final d=await context.read<AppState>().api.get('/member-portal/dashboard'); if(mounted)setState(()=>data=d); }
    catch(e){if(mounted)setState(()=>error=e.toString());}
  }
  @override void initState(){super.initState();load();}
  String money(dynamic v)=>'R$ ${v ?? '0.00'}';
  @override Widget build(BuildContext context){
    final s=(data?['summary'] as Map<String,dynamic>?) ?? {};
    final inst=(data?['installments'] as List<dynamic>?) ?? [];
    final ag=(data?['agreements'] as List<dynamic>?) ?? [];
    return Scaffold(appBar:AppBar(title:const Text('Minha prestação de contas')),body:RefreshIndicator(onRefresh:load,child:ListView(padding:const EdgeInsets.all(16),children:[
      if(error!=null) Text(error!),
      if(data!=null) ...[
        Card(child:Padding(padding:const EdgeInsets.all(16),child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
          Text(data!['member']['name'],style:Theme.of(context).textTheme.titleLarge),
          const SizedBox(height:8), Text('Dados exibidos somente da sua participação.',style:Theme.of(context).textTheme.bodySmall),
          const Divider(), Text('Contribuições pagas: ${money(s['contributions_paid'])}'),
          Text('Pagamentos de empréstimos: ${money(s['loan_payments'])}'),
          Text('Saldo de empréstimos: ${money(s['loan_outstanding'])}'),
          Text('Em atraso: ${money(s['overdue_balance'])} (${s['overdue_installments'] ?? 0} parcela(s))'),
          Text('Pontualidade: ${s['on_time_ratio']==null ? '—' : '${((s['on_time_ratio'] as num)*100).toStringAsFixed(1)}%'}'),
        ]))),
        const SizedBox(height:12), const Text('Parcelas',style:TextStyle(fontSize:20,fontWeight:FontWeight.bold)),
        ...inst.map((x){final i=x as Map<String,dynamic>;return ListTile(title:Text('Parcela ${i['number']} — ${money(i['amount'])}'),subtitle:Text('Vencimento: ${i['due_date']} • ${i['collection_stage']}'),trailing:Text(i['status']));}),
        if(ag.isNotEmpty) ...[const SizedBox(height:12),const Text('Acordos',style:TextStyle(fontSize:20,fontWeight:FontWeight.bold)),...ag.map((x){final a=x as Map<String,dynamic>;return ListTile(title:Text('Acordo #${a['id']} — ${money(a['total_amount'])}'),subtitle:Text('${a['installments']} parcelas • ${a['status']}'));})]
      ] else const Padding(padding:EdgeInsets.all(40),child:Center(child:CircularProgressIndicator()))
    ])));
  }
}
