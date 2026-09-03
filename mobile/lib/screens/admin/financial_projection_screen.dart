import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';

class FinancialProjectionScreen extends StatefulWidget { const FinancialProjectionScreen({super.key}); @override State<FinancialProjectionScreen> createState()=>_FinancialProjectionScreenState(); }
class _FinancialProjectionScreenState extends State<FinancialProjectionScreen>{
  final api=ApiClient(); Map<String,dynamic>? data; String scenario='BASE'; String? error;
  Future<void> load() async { api.token=await Session.getToken(); try{ data=await api.get('/admin/financial-projection?scenario=$scenario&horizon_months=12'); error=null; }catch(e){error=e.toString();} if(mounted)setState((){}); }
  @override void initState(){super.initState(); load();}
  @override Widget build(BuildContext context){ final rows=(data?['projection'] as List?)??[]; return Scaffold(appBar:AppBar(title:const Text('Projeção financeira')),body:Padding(padding:const EdgeInsets.all(16),child:Column(children:[DropdownButton<String>(value:scenario,items:const [DropdownMenuItem(value:'CONSERVATIVE',child:Text('Conservador')),DropdownMenuItem(value:'BASE',child:Text('Base')),DropdownMenuItem(value:'OPTIMISTIC',child:Text('Otimista'))],onChanged:(v){if(v!=null){setState(()=>scenario=v);load();}}),if(error!=null) Text(error!),if(data!=null) Card(child:ListTile(title:Text('Caixa final: R$ ${data!['ending_projected_cash']}'),subtitle:Text('Mínimo projetado: R$ ${data!['minimum_projected_cash']}'))),Expanded(child:ListView.builder(itemCount:rows.length,itemBuilder:(_,i){final r=rows[i] as Map<String,dynamic>;return ListTile(title:Text(r['month'].toString()),subtitle:Text('Fluxo líquido: R$ ${r['net_cash_flow']}'),trailing:Text('R$ ${r['projected_cash']}'));}))]))); }
}
