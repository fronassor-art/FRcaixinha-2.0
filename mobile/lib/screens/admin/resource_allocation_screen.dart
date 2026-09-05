import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';

class ResourceAllocationScreen extends StatefulWidget { const ResourceAllocationScreen({super.key}); @override State<ResourceAllocationScreen> createState()=>_ResourceAllocationScreenState(); }
class _ResourceAllocationScreenState extends State<ResourceAllocationScreen>{
 final api=ApiClient(); Map<String,dynamic>? data; final groupCtrl=TextEditingController(text:'1'); String? error;
 Future<void> load() async { api.token=await Session.getToken(); try{ data=await api.get('/admin/resource-allocation?group_id=${groupCtrl.text}'); error=null; }catch(e){error=e.toString();} if(mounted)setState((){}); }
 @override void initState(){super.initState();load();}
 @override Widget build(BuildContext context){final items=(data?['items'] as List?)?.cast<Map<String,dynamic>>() ?? []; return Scaffold(appBar:AppBar(title:const Text('Alocação de recursos'),actions:[IconButton(onPressed:load,icon:const Icon(Icons.refresh))]),body:ListView(padding:const EdgeInsets.all(16),children:[TextField(controller:groupCtrl,keyboardType:TextInputType.number,decoration:const InputDecoration(labelText:'ID do grupo'),onSubmitted:(_)=>load()),const SizedBox(height:12),if(error!=null)Text(error!),if(data!=null)...[_card('Capacidade','R\$ ${data!['capacity']}',Icons.account_balance_wallet_outlined),_card('Total recomendado','R\$ ${data!['allocated_total']}',Icons.pie_chart_outline),Text('Método: ${data!['method']}'),const SizedBox(height:12),...items.map((r)=>Card(child:ListTile(title:Text('Participante ${r['member_id']}'),subtitle:Text('Risco: ${r['risk_decision']} • Cota: ${r['quota_units']}'),trailing:Text('R\$ ${r['recommended_amount']}',style:const TextStyle(fontWeight:FontWeight.bold))))),const SizedBox(height:12),const Text('A alocação é uma recomendação. Ela não cria, aprova ou libera empréstimos automaticamente.',style:TextStyle(fontSize:13))]]));}
 Widget _card(String t,String v,IconData i)=>Card(child:ListTile(leading:Icon(i),title:Text(t),trailing:Text(v,style:const TextStyle(fontWeight:FontWeight.bold,fontSize:17))));
}
