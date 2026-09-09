import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';

class OperationalControlScreen extends StatefulWidget { const OperationalControlScreen({super.key}); @override State<OperationalControlScreen> createState()=>_OperationalControlScreenState(); }
class _OperationalControlScreenState extends State<OperationalControlScreen>{
  final api=ApiClient(); Map<String,dynamic>? data; String? error;
  @override void initState(){super.initState(); load();}
  Future<void> load() async { api.token=await Session.getToken(); try { final d=await api.get('/admin/operational-control'); if(mounted)setState(()=>data=d); } catch(e){if(mounted)setState(()=>error=e.toString());} }
  @override Widget build(BuildContext context){
    final summary=(data?['summary'] as Map?)?.cast<String,dynamic>(); final actions=(data?['actions'] as List?)?.cast<Map<String,dynamic>>()??[];
    return Scaffold(appBar:AppBar(title:const Text('Centro de Controle Operacional')),body:RefreshIndicator(onRefresh:load,child:ListView(padding:const EdgeInsets.all(16),children:[
      if(error!=null) Text(error!),
      if(data!=null) Card(child:Padding(padding:const EdgeInsets.all(16),child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[Text('Status: ${data!['status']}',style:Theme.of(context).textTheme.titleLarge),const SizedBox(height:8),Text('Ações: ${summary?['action_count']??0}  •  Críticas: ${summary?['critical']??0}  •  Altas: ${summary?['high']??0}  •  Médias: ${summary?['medium']??0}')]))),
      const SizedBox(height:8),
      ...actions.map((a)=>Card(child:ListTile(leading:Icon(a['severity']=='CRITICAL'?Icons.error:a['severity']=='HIGH'?Icons.warning_amber:Icons.info_outline),title:Text('${a['title']} (${a['count']})'),subtitle:Text(a['detail']??'')))),
      if(data==null && error==null) const Center(child:Padding(padding:EdgeInsets.all(32),child:CircularProgressIndicator()))
    ])));
  }
}
