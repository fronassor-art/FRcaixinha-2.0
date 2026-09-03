import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';

class ContinuousImprovementExecutionScreen extends StatefulWidget { const ContinuousImprovementExecutionScreen({super.key}); @override State<ContinuousImprovementExecutionScreen> createState()=>_State(); }
class _State extends State<ContinuousImprovementExecutionScreen>{ final api=ApiClient(); List items=[]; String? error;
 Future<void> load() async { api.token=await Session.getToken(); try{final d=await api.get('/admin/continuous-improvement-execution'); items=(d['items'] as List?)??[]; error=null;}catch(e){error=e.toString();} if(mounted)setState((){}); }
 Future<void> start(int id) async { api.token=await Session.getToken(); try{await api.post('/admin/continuous-improvement-execution/$id/start',{}); await load();}catch(e){if(mounted)ScaffoldMessenger.of(context).showSnackBar(SnackBar(content:Text(e.toString())));} }
 @override void initState(){super.initState();load();}
 @override Widget build(BuildContext context)=>Scaffold(appBar:AppBar(title:const Text('Execução das Melhorias')),body:RefreshIndicator(onRefresh:load,child:ListView(padding:const EdgeInsets.all(12),children:[if(error!=null)Text(error!),...items.map((x){final m=(x as Map).cast<String,dynamic>();final id=m['id'] as int;return Card(child:ListTile(title:Text('#$id — ${m['status']}'),subtitle:Text('Recomendação #${m['recommendation_id']} | responsável ${m['assigned_to']}'),trailing:m['status']=='PENDING'?IconButton(icon:const Icon(Icons.play_arrow),onPressed:()=>start(id)):null));})]));
}
