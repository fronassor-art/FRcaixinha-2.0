import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';

class ExecutiveRiskResponseScreen extends StatefulWidget { const ExecutiveRiskResponseScreen({super.key}); @override State<ExecutiveRiskResponseScreen> createState()=>_ExecutiveRiskResponseScreenState(); }
class _ExecutiveRiskResponseScreenState extends State<ExecutiveRiskResponseScreen>{ final api=ApiClient(); Map<String,dynamic>? data; String? error;
 Future<void> load() async { api.token=await Session.getToken(); try{data=await api.get('/admin/executive-risk-response');error=null;}catch(e){error=e.toString();} if(mounted)setState((){}); }
 @override void initState(){super.initState();load();}
 Widget metric(String title,dynamic value)=>Expanded(child:Card(child:Padding(padding:const EdgeInsets.all(12),child:Column(children:[Text('$value',style:Theme.of(context).textTheme.headlineSmall),Text(title,textAlign:TextAlign.center)]))));
 @override Widget build(BuildContext context){ final risk=(data?['risk'] as Map?)?.cast<String,dynamic>(); final a=(data?['alerts'] as Map?)?.cast<String,dynamic>(); final r=(data?['responses'] as Map?)?.cast<String,dynamic>(); final c=(data?['capas'] as Map?)?.cast<String,dynamic>(); final w=(data?['workflow'] as Map?)?.cast<String,dynamic>();
 return Scaffold(appBar:AppBar(title:const Text('Resposta a Riscos')),body:RefreshIndicator(onRefresh:load,child:ListView(padding:const EdgeInsets.all(12),children:[if(error!=null)Text(error!),if(data!=null)...[Card(child:ListTile(title:Text('Status executivo: ${data!['status']}'),subtitle:Text('Score de risco: ${risk?['score']??0}'))),Row(children:[metric('Alertas abertos',a?['open']??0),metric('Respostas abertas',r?['open']??0)]),Row(children:[metric('Respostas atrasadas',r?['overdue']??0),metric('CAPAs atrasadas',c?['overdue']??0)]),Row(children:[metric('Tarefas abertas',w?['open_tasks']??0),metric('Tarefas atrasadas',w?['overdue']??0)]),if((data!['risk_flags'] as List?)?.isNotEmpty==true)Card(child:Padding(padding:const EdgeInsets.all(12),child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[const Text('Prioridades'),...((data!['risk_flags'] as List).map((x)=>Text('• $x')))])))],if(data==null&&error==null)const Center(child:CircularProgressIndicator())]))); }
}
