import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';

class CapacityOptimizerScreen extends StatefulWidget { const CapacityOptimizerScreen({super.key}); @override State<CapacityOptimizerScreen> createState()=>_CapacityOptimizerScreenState(); }
class _CapacityOptimizerScreenState extends State<CapacityOptimizerScreen>{
 final api=ApiClient(); Map<String,dynamic>? data; String scenario='BASE'; String? error;
 Future<void> load() async { api.token=await Session.getToken(); try{ data=await api.get('/admin/capacity-optimizer?scenario=$scenario&horizon_months=12'); error=null; }catch(e){error=e.toString();} if(mounted)setState((){}); }
 @override void initState(){super.initState();load();}
 @override Widget build(BuildContext context){final d=data; return Scaffold(appBar:AppBar(title:const Text('Capacidade de empréstimos'),actions:[IconButton(onPressed:load,icon:const Icon(Icons.refresh))]),body:ListView(padding:const EdgeInsets.all(16),children:[DropdownButton<String>(value:scenario,isExpanded:true,items:const [DropdownMenuItem(value:'CONSERVATIVE',child:Text('Conservador')),DropdownMenuItem(value:'BASE',child:Text('Base')),DropdownMenuItem(value:'OPTIMISTIC',child:Text('Otimista'))],onChanged:(v){if(v!=null){setState(()=>scenario=v);load();}}),if(error!=null)Text(error!),if(d!=null)...[_card('Capacidade recomendada','R$ ${d['capacity']}',Icons.account_balance_wallet_outlined),_card('Capacidade mensal projetada','R$ ${d['monthly_capacity_by_projection']}',Icons.trending_up),_card('Disponível imediato','R$ ${d['immediate_capacity']}',Icons.payments_outlined),_card('Decisão','${d['decision']}',Icons.rule_folder_outlined),_card('Caixa atual','R$ ${d['current_cash']}',Icons.savings_outlined),const SizedBox(height:12),Text('Limitadores: ${(d['bottlenecks'] as List?)?.join(', ') ?? '-'}'),const SizedBox(height:12),const Text('A capacidade é uma estimativa de planejamento. A liberação real continua sujeita ao pipeline de risco, política de crédito e aprovação administrativa.',style:TextStyle(fontSize:13))]]));}
 Widget _card(String t,String v,IconData i)=>Card(child:ListTile(leading:Icon(i),title:Text(t),trailing:Text(v,style:const TextStyle(fontWeight:FontWeight.bold,fontSize:17))));
}
