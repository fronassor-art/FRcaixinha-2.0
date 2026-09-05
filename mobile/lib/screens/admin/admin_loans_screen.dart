import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';

class AdminLoansScreen extends StatefulWidget { const AdminLoansScreen({super.key}); @override State<AdminLoansScreen> createState()=>_AdminLoansScreenState(); }
class _AdminLoansScreenState extends State<AdminLoansScreen>{
  final api=ApiClient(); List<dynamic> items=[]; String? error;
  @override void initState(){super.initState();load();}
  Future<void> load() async { api.token = await Session.getToken(); try{final r=await api.get('/admin/loans?status=REQUESTED');items=r['items']??[];if(mounted)setState(()=>error=null);}catch(e){if(mounted)setState(()=>error=e.toString());} if(mounted)setState((){}); }
  Future<void> decide(int id,bool approve) async { try{await api.post('/loans/$id/decision',{'approve':approve});await load();}catch(e){if(mounted)ScaffoldMessenger.of(context).showSnackBar(SnackBar(content:Text(e.toString())));} }
  @override Widget build(BuildContext context)=>Scaffold(appBar:AppBar(title:const Text('Solicitações de empréstimo')),body:RefreshIndicator(onRefresh:load,child:ListView(padding:const EdgeInsets.all(12),children:[if(error!=null)Text(error!),if(error==null && items.isEmpty)const Card(child:Padding(padding:EdgeInsets.all(20),child:Center(child:Text('Nenhuma solicitação de empréstimo pendente.')))),...items.map((x)=>Card(child:ListTile(title:Text(x['member_name']??'Membro'),subtitle:Text('R\$ ${x['principal']} • ${x['installments']} parcelas • taxa ${x['monthly_rate']}'),trailing:Wrap(spacing:4,children:[IconButton(tooltip:'Rejeitar',onPressed:()=>decide(x['id'],false),icon:const Icon(Icons.close)),IconButton(tooltip:'Aprovar',onPressed:()=>decide(x['id'],true),icon:const Icon(Icons.check))]))))])));
}
